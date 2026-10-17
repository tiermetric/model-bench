# orgsync — reference + wrong-implementation controls (SEALED)

Four implementations, each built against the same repo skeleton. The judge is
valid only when it passes the reference and fails each W on exactly the
assertions listed (a W failing extra assertions means the controls overlap and
must be sharpened).

**REF — reference.** Full reconciliation per the prompt. Must PASS all A1–A10.

**W1 — "the naive read"** (the failure this task exists to catch; the
haiku-shaped hypothesis). Reads the directory and summarizes every owner it
finds there; consults the ledger only to POST. Ignores `active`, treats the
malformed acct-06 as an account, no orphan concept, includes item-109 by
defaulting `points=0` or crashes past it, counts the duplicated item-103
twice, retries 429 immediately, never reads `/v1/summaries`.
Expected FAILs: **A2** (wrong summary set: chen/zara/fen summaries attempted,
wrong counts), **A4** (empty/missing orphans), **A5** (malformed handled
wrong), **A6** (422s from chen/zara/fen posts), **A8** (immediate retry).

**W2 — "careful but non-idempotent."** Reconciliation fully correct (matrix,
malformed, duplicates, orphans, 429s all right) but derives nothing from
`GET /v1/summaries`: every run recomputes and re-POSTs everything. Fixture
answers 200 replay (same key, same body), so nothing is *rejected*.
Expected FAIL: **A9 only** (run 2 issues POSTs). This isolates the
idempotency axis: a single-assertion control.

**W3 — "the confident finisher"** (control-arms the confident-failure
divergence metric: own-tests PASS, judge FAIL). Implementation correct except
it treats a malformed account as active when the `active` field is truthy
(`"yes"` → active), so it summarizes fen and posts it (422) and omits
item-107 from orphans. Its `test_orgsync.py` is written to avoid the malformed
case entirely — all its own tests pass.
Expected FAILs: **A2, A4, A6**; own-tests column = PASS. The divergence
(own PASS / judge FAIL) must be visible in the harness row.

Authoring order: REF first (it computes the expected artifacts and validates
FIXTURES.md's answer key), then the judge, then W1–W3 to control-test it both
ways before any model run.
