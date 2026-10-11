#!/usr/bin/env python3
"""hang-line-staging -- an ephemeral "clothesline" for agent output.

Mental model: agent work products hang on a virtual clothesline.
  * hang   : put bytes on the line (in memory only, default zero disk writes)
  * peek   : inspect the line without taking anything down
  * take   : explicitly take an item down -- THE ONLY PATH THAT WRITES TO DISK
  * sweep  : discard items older than TTL (they simply fall off and are gone)

Default mode is fully ephemeral: kill the process and the line is empty.
An opt-in `--line FILE` keeps a single state file so the line survives
across CLI invocations -- one file, explicitly chosen by the user.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time
import uuid

VERSION = "0.1.1"
DEFAULT_TTL = 600.0
DEFAULT_MAX_ITEMS = 256
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

LINE_FORMAT = "hang-line/1"


class LineError(Exception):
    exit_code = 3


class UnknownItem(LineError):
    pass


class LineFull(LineError):
    pass


class CorruptLineFile(LineError):
    exit_code = 4


class DestExists(LineError):
    exit_code = 5


def atomic_write(dest: str, data: bytes) -> None:
    """Write bytes to dest via a temp file + rename (never a partial file)."""
    ddir = os.path.dirname(os.path.abspath(dest)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".hangline-", dir=ddir)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class Clothesline:
    def __init__(self, ttl: float = DEFAULT_TTL, clock=time.time,
                 max_items: int = DEFAULT_MAX_ITEMS, max_bytes: int = DEFAULT_MAX_BYTES):
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self.ttl = ttl
        self._clock = clock
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._items: dict = {}  # id -> {"name","data","meta","hung_at"}

    # -- line operations ----------------------------------------------------
    def hang(self, data: bytes, name: str | None = None, meta: dict | None = None) -> str:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("hang expects bytes")
        data = bytes(data)
        if len(data) > self.max_bytes:
            raise LineFull(f"item is {len(data)} bytes, max_bytes={self.max_bytes}")
        self.sweep()  # taking out the trash before pinning something new
        if len(self._items) >= self.max_items:
            raise LineFull(f"line holds {self.max_items} items already; take one or sweep")
        item_id = uuid.uuid4().hex[:12]
        self._items[item_id] = {"name": name or f"item-{item_id}",
                                "data": data, "meta": dict(meta or {}),
                                "hung_at": self._clock()}
        return item_id

    def sweep(self) -> list:
        """Silently discard expired items. Returns the ids that fell off."""
        now = self._clock()
        expired = [i for i, it in self._items.items() if now - it["hung_at"] > self.ttl]
        for i in expired:
            del self._items[i]
        return expired

    def peek(self, item_id: str | None = None) -> dict | list:
        """One item (without payload bytes) or the whole line listing."""
        self.sweep()
        if item_id is None:
            now = self._clock()
            out = []
            for i, it in self._items.items():
                out.append({"id": i, "name": it["name"], "size": len(it["data"]),
                            "meta": it["meta"],
                            "ttl_left": round(self.ttl - (now - it["hung_at"]), 1)})
            return out
        it = self._items.get(item_id)
        if it is None:
            raise UnknownItem(f"no item {item_id} on the line (taken, swept, or never hung)")
        now = self._clock()
        return {"id": item_id, "name": it["name"], "size": len(it["data"]),
                "meta": it["meta"], "ttl_left": round(self.ttl - (now - it["hung_at"]), 1)}

    def take(self, item_id: str, dest: str, force: bool = False) -> dict:
        """THE ONLY disk-write path. Removes the item from the line."""
        self.sweep()
        it = self._items.get(item_id)
        if it is None:
            raise UnknownItem(f"no item {item_id} on the line (taken, swept, or never hung)")
        if os.path.exists(dest) and not force:
            raise DestExists(f"dest {dest} exists; pass force=True / --force to overwrite")
        atomic_write(dest, it["data"])
        del self._items[item_id]
        return {"id": item_id, "name": it["name"], "dest": dest, "size": len(it["data"])}

    # -- (de)serialization for opt-in persistent mode ------------------------
    def to_json(self) -> str:
        self.sweep()
        return json.dumps({"format": LINE_FORMAT, "ttl": self.ttl,
                           "items": [{"id": i, "name": it["name"], "meta": it["meta"],
                                      "hung_at": it["hung_at"],
                                      "data_b64": base64.b64encode(it["data"]).decode()}
                                     for i, it in self._items.items()]},
                          ensure_ascii=False)

    def save_json(self, path: str) -> None:
        atomic_write(path, self.to_json().encode("utf-8"))

    @classmethod
    def from_json(cls, path: str, clock=time.time) -> "Clothesline":
        try:
            with open(path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
        except json.JSONDecodeError as e:
            raise CorruptLineFile(f"line file {path} is not valid JSON: {e}")
        except OSError as e:
            raise LineError(f"cannot read line file: {e}")
        if not isinstance(blob, dict) or blob.get("format") != LINE_FORMAT:
            raise CorruptLineFile(f"line file {path} lacks format={LINE_FORMAT!r} marker")
        line = cls(ttl=float(blob.get("ttl", DEFAULT_TTL)), clock=clock)
        for it in blob.get("items", []):
            line._items[str(it["id"])] = {
                "name": str(it.get("name", "")), "meta": dict(it.get("meta", {})),
                "hung_at": float(it["hung_at"]),
                "data": base64.b64decode(it.get("data_b64", ""))}
        line.sweep()
        return line


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_or_new(args) -> Clothesline:
    if getattr(args, "line", None) and os.path.exists(args.line):
        return Clothesline.from_json(args.line)
    return Clothesline(ttl=args.ttl)


def _cmd_hang(args) -> int:
    line = _load_or_new(args)
    data = open(args.file, "rb").read() if args.file else args.text.encode("utf-8")
    item_id = line.hang(data, name=args.name, meta={"source": args.file or "argv"})
    print(f"hung {item_id}  name={args.name or '(auto)'}  size={len(data)}")
    if not args.line:
        print("note: ephemeral line -- this dies with the process. "
              "Use --line STATE for a persistent line, or take it now.")
    if args.line:
        line.save_json(args.line)
        print(f"line state -> {args.line}")
    if args.take_to:
        r = line.take(item_id, args.take_to)
        if args.line:
            line.save_json(args.line)
        print(f"taken -> {r['dest']} ({r['size']} bytes)")
    return 0


def _cmd_list(args) -> int:
    line = _load_or_new(args)
    items = line.peek()
    if not items:
        print("(line is empty)")
    for it in items:
        print(f"{it['id']}  {it['size']:>10} B  ttl {it['ttl_left']:>7.1f}s  {it['name']}")
    return 0


def _cmd_take(args) -> int:
    line = _load_or_new(args)
    r = line.take(args.id, args.to, force=args.force)
    if args.line:
        line.save_json(args.line)
    print(f"taken {r['id']} -> {r['dest']} ({r['size']} bytes); item left the line")
    return 0


def _cmd_sweep(args) -> int:
    line = _load_or_new(args)
    swept = line.sweep()
    if args.line:
        line.save_json(args.line)
    print(f"swept {len(swept)} item(s): {' '.join(swept) if swept else '(none expired)'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hangline", description="ephemeral clothesline staging")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--ttl", type=float, default=DEFAULT_TTL, help=f"item TTL seconds (default {DEFAULT_TTL:g})")
        sp.add_argument("--line", metavar="STATE", help="opt-in persistent line state file")

    h = sub.add_parser("hang", help="hang a file's bytes (--file) or text (--text) on the line")
    common(h)
    h.add_argument("--file", help="read item bytes from this file")
    h.add_argument("--text", help="or hang literal text")
    h.add_argument("--name", help="human label")
    h.add_argument("--take-to", metavar="DEST", help="immediately take it down to DEST (atomic)")
    h.set_defaults(func=_cmd_hang)

    l = sub.add_parser("list", help="peek at the line")
    common(l)
    l.set_defaults(func=_cmd_list)

    t = sub.add_parser("take", help="take an item down and write it to disk (only disk-write path)")
    common(t)
    t.add_argument("id")
    t.add_argument("--to", required=True, dest="to", metavar="DEST")
    t.add_argument("--force", action="store_true", help="overwrite existing DEST")
    t.set_defaults(func=_cmd_take)

    s = sub.add_parser("sweep", help="discard expired items now")
    common(s)
    s.set_defaults(func=_cmd_sweep)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "hang" and getattr(args, "file", None) and getattr(args, "text", None):
        print("error: --file and --text are mutually exclusive", file=sys.stderr)
        return 2
    if args.cmd == "hang" and getattr(args, "file", None) is None and getattr(args, "text", None) is None:
        print("error: hang needs --file or --text", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except LineError as e:
        print(f"error: {e}", file=sys.stderr)
        return getattr(e, "exit_code", 3)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
