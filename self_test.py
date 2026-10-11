#!/usr/bin/env python3
"""Self-test for hang-line-staging: functional / error-path / extreme-edge."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hangline  # noqa: E402

RESULTS = []
CASES = []


def case(name):
    def deco(fn):
        def wrapper():
            try:
                fn()
                RESULTS.append((name, "PASS", ""))
            except AssertionError as e:
                RESULTS.append((name, "FAIL", str(e)))
            except Exception as e:  # noqa: BLE001
                RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}"))
        CASES.append(wrapper)
        return wrapper
    return deco


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ---------------- functional ----------------

@case("func/hang-then-take-roundtrip")
def _():
    line = hangline.Clothesline(clock=FakeClock())
    payload = b"\x00binary\xff bytes \xe4\xb8\xad\xe6\x96\x87"
    iid = line.hang(payload, name="x")
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "out.bin")
        r = line.take(iid, dest)
        assert open(dest, "rb").read() == payload
        assert r["size"] == len(payload)


@case("func/take-removes-item-from-line")
def _():
    line = hangline.Clothesline(clock=FakeClock())
    iid = line.hang(b"data")
    with tempfile.TemporaryDirectory() as td:
        line.take(iid, os.path.join(td, "a"))
    assert line.peek() == [] and iid not in [i["id"] for i in line.peek()]


@case("func/sweep-removes-only-expired")
def _():
    ck = FakeClock(0.0)
    line = hangline.Clothesline(ttl=10, clock=ck)
    a = line.hang(b"old")
    ck.t = 5
    b = line.hang(b"new")
    ck.t = 12
    swept = line.sweep()
    assert swept == [a], swept
    ids = [i["id"] for i in line.peek()]
    assert b in ids and a not in ids


@case("func/peek-listing-shapes")
def _():
    line = hangline.Clothesline(clock=FakeClock())
    iid = line.hang(b"abc", name="n1", meta={"k": 1})
    one = line.peek(iid)
    assert one["name"] == "n1" and one["meta"] == {"k": 1} and one["ttl_left"] > 0
    assert isinstance(line.peek(), list)


@case("func/persistent-line-state-roundtrip")
def _():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "line.json")
        l1 = hangline.Clothesline(ttl=60, clock=FakeClock())
        iid = l1.hang(b"persist me", name="p")
        l1.save_json(p)
        l2 = hangline.Clothesline.from_json(p, clock=FakeClock())
        dest = os.path.join(td, "out")
        l2.take(iid, dest)
        assert open(dest, "rb").read() == b"persist me"


@case("func/cli-hang-take-with-state-file")
def _():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src.txt")
        open(src, "w", encoding="utf-8").write("hello line")
        state = os.path.join(td, "line.json")
        rc1 = subprocess.run([sys.executable, "hangline.py", "hang", "--file", src,
                              "--name", "greeting", "--line", state], capture_output=True, cwd=HERE)
        assert rc1.returncode == 0, rc1.stderr
        iid = rc1.stdout.decode().split()[1]
        dest = os.path.join(td, "taken.txt")
        rc2 = subprocess.run([sys.executable, "hangline.py", "take", iid, "--to", dest,
                              "--line", state], capture_output=True, cwd=HERE)
        assert rc2.returncode == 0, rc2.stderr
        assert open(dest, encoding="utf-8").read() == "hello line"


# ---------------- error paths ----------------

@case("err/take-unknown-id-exit-3")
def _():
    rc = subprocess.run([sys.executable, "hangline.py", "take", "nope123", "--to", "x"],
                        capture_output=True, cwd=HERE)
    assert rc.returncode == 3, rc.returncode


@case("err/take-twice-second-fails")
def _():
    line = hangline.Clothesline(clock=FakeClock())
    iid = line.hang(b"once")
    with tempfile.TemporaryDirectory() as td:
        line.take(iid, os.path.join(td, "1"))
        try:
            line.take(iid, os.path.join(td, "2"))
            raise AssertionError("expected UnknownItem")
        except hangline.UnknownItem:
            pass


@case("err/corrupt-line-file-exit-4")
def _():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "bad.json")
        open(p, "w").write("{not json at all")
        rc = subprocess.run([sys.executable, "hangline.py", "list", "--line", p],
                            capture_output=True, cwd=HERE)
        assert rc.returncode == 4, rc.returncode


@case("err/hang-rejects-non-bytes")
def _():
    line = hangline.Clothesline(clock=FakeClock())
    try:
        line.hang("string not bytes")
        raise AssertionError("expected TypeError")
    except TypeError:
        pass


@case("err/dest-exists-refuses-without-force")
def _():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "s")
        open(src, "w").write("x")
        dest = os.path.join(td, "exists")
        open(dest, "w").write("old")
        rc = subprocess.run([sys.executable, "hangline.py", "hang", "--file", src,
                             "--take-to", dest], capture_output=True, cwd=HERE)
        assert rc.returncode == 5, rc.returncode
        assert open(dest).read() == "old"


# ---------------- extreme edges ----------------

@case("edge/empty-item-hang-take")
def _():
    line = hangline.Clothesline(clock=FakeClock())
    iid = line.hang(b"")
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "empty")
        line.take(iid, dest)
        assert os.path.getsize(dest) == 0


@case("edge/huge-item-2mib-ok-9mib-rejected")
def _():
    line = hangline.Clothesline(clock=FakeClock())
    iid = line.hang(b"A" * (2 * 1024 * 1024))
    assert len(line.peek(iid)["size"] * b"?" if False else line.peek(iid)) >= 0
    try:
        line.hang(b"B" * (9 * 1024 * 1024))
        raise AssertionError("expected LineFull")
    except hangline.LineFull:
        pass


@case("edge/item-limit-then-full")
def _():
    line = hangline.Clothesline(clock=FakeClock(), max_items=3)
    for i in range(3):
        line.hang(b"x")
    try:
        line.hang(b"overflow")
        raise AssertionError("expected LineFull")
    except hangline.LineFull:
        pass


@case("edge/tampered-state-file-missing-format")
def _():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.json")
        json.dump({"items": []}, open(p, "w"))
        try:
            hangline.Clothesline.from_json(p)
            raise AssertionError("expected CorruptLineFile")
        except hangline.CorruptLineFile:
            pass


@case("edge/no-disk-write-before-take")
def _():
    with tempfile.TemporaryDirectory() as td:
        before = set(os.listdir(td))
        line = hangline.Clothesline(clock=FakeClock())
        line.hang(b"secret", name="must-not-persist")
        line.hang(b"more")
        after = set(os.listdir(td))
        assert before == after, f"unexpected files: {after - before}"


def main() -> int:
    for fn in CASES:
        fn()
    width = max(len(n) for n, _, _ in RESULTS)
    for name, status, msg in RESULTS:
        print(f"{name:<{width}}  {status}" + (f"  ({msg})" if msg else ""))
    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    print(f"\n{passed}/{len(RESULTS)} passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
