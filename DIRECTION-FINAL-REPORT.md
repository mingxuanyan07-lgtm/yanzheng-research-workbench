# Direction provenance / preview / robustness — final handoff

## Scope

This branch closes the non-human direction-flow gaps:

- precise binding of direction `supports`/`limits` evidence to a confirmed reading note (`note_id`), with note/content hashes and locator snapshot;
- direction revision preview plus explicit accept/reject; `/advance` no longer applies automatically;
- declared source version / SHA256 chain on reading notes (declared only; the program never reads or authenticates the original);
- stale-preview UI: context changes mark pending proposal stale and disable accept while keeping reject available;
- proposal history capped at 50 with `proposals_pruned_count`, active pending preserved;
- skills direction-map validator accepts and validates `note_id` + `note_snapshot`, fail closed for malformed bindings.

Legacy `/generate` is unchanged (direct application). No main/candidate merge, no ZIP/freeze/formal acceptance change.

## Reproduction

Worktree: `D:\天猫AI-feature-direction`
Branch: `feature/direction-provenance-preview`
Base candidate: `31c787a2fea847dd107fa613052debd814ed6bcc`
Remote branch: <https://github.com/mingxuanyan07-lgtm/yanzheng-research-workbench/tree/feature/direction-provenance-preview>

```powershell
cd D:\天猫AI-feature-direction
webapp\.venv\Scripts\python.exe -X utf8 -B scripts\run_tests.py --suite all
node --test webapp\tests\frontend\*.cjs
```

Lead rerun (2026-10-20):

- full `--suite all`: **324 tests, 0 failures, 0 errors, 4 skipped** (skips are the existing Windows symlink capability limitation, WinError 1314);
- frontend three files: **90/90 passed**;
- real Chrome 155, isolated temp DB on port 18517: **33/33 checks**, including pending panel, accept, reject, stale flag, disabled stale accept, reject enabled, no pageerrors.

Browser evidence is under `D:\天猫AI\output\lead-review\20261020-direction-browser\`; it used a fixture model, no real model call, no real research DB, no Lean.

## Residual / explicitly not done

1. Declared `source_version`/`source_sha256` is a user declaration only; the app does not fetch/read/authenticate the original file. It is not a paper-version or content-authentication claim.
2. Older direction maps with `supports`/`limits` but without `note_id`/`note_snapshot` now fail the tightened contract; no migration was added.
3. `/advance` semantic change: external consumers must use the new accept/reject endpoints; legacy `/generate` remains direct.
4. Stale flag is derived on project GET and persisted nowhere; other mutate responses do not include it.
5. Preview is summary-level diff; reject reason is available in API but not exposed as a UI input; no per-field full diff.
6. Proposal history is bounded at 50; older entries are pruned and only a count is retained.
7. Real browser E2E covers accept/reject/stale; no real model, real Lean, real research DB, service deployment, or original DB startup was performed.
8. Not Codex-accepted, not merged to `main`, not a formal release candidate acceptance.

## What Codex should check

- branch/commit ancestry from `31c787a2`;
- tests above, especially the 4 skips are environment capability skips not silent passes;
- direction evidence snapshot shape and declared-fingerprint caveat;
- `/advance` pending semantics and accept/reject stale behavior;
- skills validator fail-closed changes;
- that no main/candidate/ZIP/freeze file changed and no formal claim is made.

This report is engineering handoff, not formal acceptance or scientific validation.
