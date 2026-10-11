# hang-line-staging

An ephemeral "clothesline" for agent output. Work products hang on a virtual line in memory; they only reach disk when you explicitly `take` one down, and anything left past its TTL falls off and is gone. If you want staging with zero accidental persistence, this is it; if you want durable queues or pub/sub, look elsewhere.

**As of 2026-10-11.**

## Problem

Agent pipelines constantly produce intermediate artifacts — rendered drafts, fetched payloads, generated snippets. Conventional staging writes every artifact to a temp directory "just in case". That default is wrong in three ways:

1. Disk fills with artifacts nobody ever consumed, and stale artifacts get mistaken for current ones.
2. Sensitive intermediate output (tokens, drafts, fetched private data) silently outlives its purpose.
3. There is no visible inventory of "what is waiting to be picked up".

The clothesline inverts the default: **nothing touches disk until a human or agent explicitly takes it down**. Forgetting about an item is the *cleanup* path, not the retention path.

## How it works

Four operations, one invariant (`take` is the only disk-write path):

| Op | Behavior |
|---|---|
| `hang(data)` | Store bytes in memory; get an ID. Rejects if over `max_bytes` or `max_items`. Sweeps expired items first. |
| `peek([id])` | Inventory without payload: name, size, remaining TTL. |
| `take(id, dest)` | Atomically write bytes to `dest` (temp file + rename), then remove from the line. Refuses to overwrite without `force`. |
| `sweep()` | Discard items older than TTL. Their content is gone — never spooled to disk first. |

Default mode is a single-process, zero-disk-write line. An opt-in `--line STATE.json` persists the line (content encoded in exactly one file you chose) so separate CLI invocations can hang and take. TTL defaults to 600 s and is injectable — a fake clock makes expiry fully deterministic in tests.

## Quickstart

```bash
# ~10 s: single-process demo — hang, take down, verify
python hangline.py hang --text "draft v1" --name draft --take-to ./draft.txt
cat draft.txt

# ~10 s: persistent line across CLI invocations
python hangline.py hang --file report.md --line line.json     # 1st shell
python hangline.py list --line line.json                      # 2nd shell
python hangline.py take <id> --to ./report.md --line line.json

# ~5 s: full self-test (16 cases)
python self_test.py
```

Library use: `from hangline import Clothesline` — pure stdlib, no config files.

## Why not X

| Alternative | Why not alone |
|---|---|
| System temp dir + cron cleanup | Everything persists immediately; cleanup is a background guess, not an explicit act; no inventory |
| Redis / memcached | Real TTL support, but a running service, ports, and ops burden for what is often a single-process handoff |
| Clipboard managers | Human-only, unscriptable, and content silently overwrites |
| Message queues (SQS/NATS) | Built for guaranteed delivery; here *loss on abandonment* is the feature, not a bug |

## Minimal Fallback

No Python? `hang --take-to DEST` in one command is reproducible with `mktemp` + `mv` in POSIX shell — you lose TTL, inventory, and expiry, but the "explicit destination or nothing" discipline survives. The library is also small enough (~250 lines) to inline into any Python 3.9+ project.

## Design Trade-offs

Priority order when goals conflict: **no accidental persistence > determinism > convenience**.

- In-memory default means a crash loses everything: accepted, because that is the product (fresh line, no residue).
- Opt-in `--line` stores payload bytes inside the state file (base64): one explicit file beats scattering temp files; documented rather than hidden.
- `take` removes the item: matches the clothesline metaphor; re-take after `--force` loss is impossible, by design.
- UUID-prefixed IDs instead of sequential: no intent to imply an ordered, durable log.
- Content-addressing rejected: staging is about *handoff*, not dedup; identity = the hang event.

## Limits & Second-order Effects

- **Limits.** Single process by default (cross-invocation requires `--line`); items are plain bytes (no streaming — a hang of 2 MiB is 2 MiB in RAM); TTL granularity is seconds; no encryption of the state file.
- **Second-order effects.** Because forgetting is safe, agents may stop finishing the last mile and leave everything to TTL expiry — wasted work, and a "why is my output missing" support class. The opt-in state file can itself become a forgotten persistence hole; it is one obvious file, but someone must still delete it. Teams may also start treating the line as a database; `max_items` (256) and `max_bytes` (8 MiB) are there to make that fail loudly instead.

## FAQ

**Q: Where do expired items go?** Nowhere. Sweep drops them from memory; no spool file, no graveyard.

**Q: Is `--line` a database?** No — a JSON file with an explicit `hang-line/1` format marker; a truncated or tampered file is rejected (exit code 4), never partially trusted.

**Q: How deterministic is expiry?** Fully: inject a fake clock; the sweep test asserts exact expired-ID sets.

**Q: Can two processes share the default line?** No — by design. Use `--line` and accept the one-file trade-off, or restructure to a single process.

**Q: When NOT to use this?** (Socratic check) Not for anything you cannot afford to lose on process exit (use real storage), not for high-throughput handoff (in-memory copies), not for multi-consumer fan-out (a taken item is gone), and not as a secrets vault (the state file is plaintext base64).

## Core Assumptions

- [A1] "Default-no-persist, explicit-take-persists" maps to real agent handoff better than temp-dir-plus-gc. *(High confidence — verified in daily agent output flows, though adoption evidence is anecdotal.)*
- [A2] TTL loss being *silent* is desirable; a dead-letter queue would recreate the retention problem. *(Medium confidence — some users will want a tombstone log.)*
- [A3] Single-writer semantics are acceptable for staging; queues exist for multi-consumer needs. *(High confidence.)*
- [A4] 8 MiB / 256 items caps match typical agent artifacts (reports, patches, small media). *(Medium confidence — [estimated] from local task logs, no cross-project benchmark.)*

*Measured marker: self-test suite (16/16 PASS, repro `python self_test.py`) verifies zero-disk-write-before-take, exact TTL expiry, and corrupt-state rejection. Knowledge cutoff: 2026-10-11.*
