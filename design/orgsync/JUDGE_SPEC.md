# orgsync — judge assertion list (SEALED; builds `acceptance_orgsync.py`)

Procedure (stated to the model in the prompt's Acceptance bullet): the judge
starts a fresh fixture (FIXTURES.md §2), rewrites `config.json`, runs
`python3 -m orgsync sync` twice (each under the harness timeout), then
asserts. PASS = all assertions hold. Every assertion maps to a prompt
sentence; **any assertion that loses its prompt sentence is cut** (PROTOCOL
§3.2). The `tests/existing` rerun and the request-log read are the only
evidence sources besides the three output files.

| # | assertion | prompt sentence that states it |
|---|---|---|
| A1 | `python3 -m unittest discover tests/existing` exits 0, with `tests/existing/` files unmodified (hash check vs skeleton) | "files under `tests/existing/` must not be edited, and `python3 -m unittest discover tests/existing` must still pass" |
| A2 | The set of accepted POST bodies (fixture state after run 1) equals exactly the four expected summaries (FIXTURES §4) | Rule 2 ("The ledger decides who counts…", zero-item and inactive clauses) + Rule 5 ("Post what the ledger is missing, and only that") |
| A3 | `out/summary.jsonl`, parsed as a set, equals the accepted POST bodies | Rule 5: "Record each summary you post as one JSON line (exactly the POST body)" |
| A4 | `out/orphans.jsonl`, parsed as a set, equals exactly {item-104, item-106, item-107}; no duplicates | Rule 3 ("Items without an active owner are orphans… exactly once") + Rule 4's "a malformed account is not an account" (item-107) |
| A5 | `out/errors.jsonl`, parsed as a set, equals exactly {acct-06 (ledger), item-109 (directory)}; no duplicates; run exited 0 | Rule 4 ("Malformed records never stop the run and never enter any count… exactly once") |
| A6 | Request log: (a) at least one write actually happened, and (b) zero writes answered 400/409/422, across both invocations. A 429 on a write is EXPECTED and legal — the fixture schedule guarantees one, and rule 6 governs it. | Rule 5 (as corrected): "never send a POST that the ledger *rejects* — that is, one it answers 400, 409 or 422. A 429 is not a rejection; it is the rate limit of rule 6, and the same POST is retried after waiting." |
| A7 | Request log: every page of `/v1/accounts`, `/v1/items`, `/v1/summaries` requested at least once in run 1 | Rule 1: "Retrieve every page… by following `next_page` until it is `null`" |
| A8 | Request log: for each 429 served, the next request to that service is ≥ Retry-After − 0.05s later, and the 429'd request was eventually retried (work completed) | Rule 6: "send that service no further request until at least Retry-After seconds have elapsed, then retry… never abandons the work" |
| A9 | Run 2: zero POST requests in the log; the three output files parse to the same sets as after run 1; exit 0 | Rule 7: "Running `sync` again… must issue no write requests and must leave the three output files with the same content" |
| A10 | Idempotency keys on accepted POSTs are exactly `sync-<owner>`, one per summary | Rule 5: "POST it with header `Idempotency-Key: sync-<owner>`" |

Harness-level (not judge assertions, already enforced): web-tool void (§4.4),
spend > 0, exactly one session log. Own-tests (`test_orgsync`) remain
**diagnostic only, never scored** (PROTOCOL §2) — captured for the
confident-failure divergence metric.

Control-testing requirement before first use (PROTOCOL §4.5): reference PASS ·
W1 FAIL · W2 FAIL · W3 FAIL, with each W failing on exactly its designed
assertions (below), and no others.


---

## Corrections applied after building the reference and the judge

**A6 (2026-07-22).** This spec originally read "zero write requests answered
4xx". That was unsatisfiable: the fixture's rate-limit schedule guarantees a
429 lands on a write, because every correct implementation makes the same five
ledger reads before its first POST, so the writes always occupy the ordinals
where the limit fires. A deliberately wasteful variant that re-reads before
every write was tested and lands on one too. 429 is a 4xx, so every correct
implementation would have failed. The prompt's rule 5 was corrected to name
rejection explicitly, and this row now matches it. The 429-on-a-write is kept
deliberately — it catches an implementation that retries a write with a fresh
idempotency key and double-posts.

**A6 and A8 gained vacuity preconditions.** Both are "no bad thing happened"
assertions and would pass on a target that never writes or is never rate
limited. Each now asserts that its own precondition actually fired.

**CONTROLS.md's predicted failure sets are wrong in two places** — a
documentation defect, not a defect in the controls or the judge. W3 cannot fail
A2: A2 is judged from POSTs the ledger *accepted*, and the ledger itself
rejects W3's bogus summary with a 422, so the accepted set stays correct and
the defect surfaces at A3 instead. W1's listed set is necessarily a subset of
what it trips, because "never reads /v1/accounts or /v1/summaries" is part of
W1's definition and that is precisely what A7 checks. Measured behaviour is
authoritative; do not "fix" the judge to match the prediction.
