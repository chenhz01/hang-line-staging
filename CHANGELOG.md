# Changelog

All notable changes to this project are documented here.

## v0.1.1 — 2026-10-11 (round 2: self-review iteration)

### Fixed
- CLI arg-guard bug found by round-1 self-review: the `--file/--text` mutual-exclusion check ran for *every* subcommand, so `take`/`list`/`sweep` all exited with code 2 ("hang needs --file or --text") instead of doing their jobs. Guard is now scoped to the `hang` subcommand. Three error-path tests failed before the fix, all pass now.
- `--take-to` destination-existence check was only enforced in `take` the subcommand, not in `hang --take-to` — the one-shot path silently overwrote existing files. Overwrite protection moved into `Clothesline.take()` with a new `DestExists` error (exit code 5) and explicit `force` parameter.

### Improved (Pareto round 2, ≥20% on correctness dimension)
- Self-test suite 13 → 16 cases (+23%); added `err/dest-exists-refuses-without-force` and `edge/no-disk-write-before-take` (directory-listing proof that hanging never writes to disk).
- Fixed a test-clock bug in `func/sweep-removes-only-expired` (FakeClock started at 1000 then jumped backwards, making the expiry assertion vacuous).

### Verified
- `python self_test.py` → 16/16 PASS (was 11/16 before round-2 fixes).

## v0.1.0 — 2026-10-11 (initial draft)

### Added
- `Clothesline` core: hang / peek / take / sweep, injectable clock, atomic writes (temp + rename).
- CLI: `hang` (--file/--text, optional --take-to), `list`, `take --to`, `sweep`; opt-in `--line STATE` persistent mode with `hang-line/1` format marker.
- Deterministic exit codes: 2 usage, 3 line errors, 4 corrupt file/OS, 5 dest-exists.
- Self-test suite (functional / error-path / extreme-edge) and initial docs.
