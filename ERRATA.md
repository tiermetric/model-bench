# Versioned report errata

Published `REPORT.md`, `MANIFEST.json`, run bundles, and sealed artifacts remain
unchanged. Corrections are appended as edition-local sidecars. This mechanism
does not author corrections or render reports/PDFs.

Prepare a JSON list of **new** entries. Each entry has exactly these fields:

| Field | Value |
| --- | --- |
| `date` | Correction date, `YYYY-MM-DD` |
| `claim` | Affected claim, including its report location |
| `replacement` | Replacement or qualification |
| `evidence` | Nonempty list of public references: public relative files, commits, issue numbers, or HTTPS links |

Run `python3 -B errata.py editions/2026-10 --entries /path/to/new-entries.json`
(or use `editions/2026-07`). The input file is operator-local; its path is never
recorded in the published entries. Entry strings must pass the existing
identity/redaction scan; evidence cannot contain local paths.

The command exclusively creates `ERRATA.v1.json` and `MANIFEST.v1.json`, then
`v2`, `v3`, etc. Errata documents have `schema_version: 1`, `edition`,
`revision`, and cumulative `entries`. Each later version retains every earlier
entry verbatim and appends new entries. Correct an earlier erratum by adding a
new entry; do not edit it. Dates are editorial correction dates, not independently
witnessed timestamps.

Successor manifests preserve all original manifest fields and add:

- `revision`: the sidecar revision number;
- `predecessor`: `{ "path": "MANIFEST.json", "sha256": "…" }` for v1,
  or the preceding `MANIFEST.vN.json` for later revisions;
- `errata`: `{ "path": "ERRATA.vN.json", "sha256": "…" }`.

SHA-256 hashes cover exact file bytes, including whitespace and final newlines.
The highest contiguous revision is current; all earlier revisions must remain
available. The original manifest remains the authority for runs and figures.
Revisions may only add errata metadata, never change original manifest fields.

`export.py --out` copies every revision byte-for-byte through the existing sealed
path/content boundary and identity scan. Both dry-run and actual export validate
the staged chain against the source file list before writing the destination.
An omitted sidecar or revision refuses the export.

`verify.py editions/2026-10` and `verify.py editions/2026-07` check hashes, schema,
predecessors, and append-only history. At the kit/repository root, the verifier
checks every directory under `editions/`. It always prints, for each edition,
`errata revision N, newest manifest sha256 <hash>`. With no sidecars, N is 0 and
the hash covers `MANIFEST.json`; the original check counts remain unchanged.
Malformed, orphaned, symlinked, or tampered retained chains fail verification.

Without a pin, verification cannot establish freshness: a valid chain may have
had its newest revisions, or all its sidecars, removed. Obtain the revision and
newest manifest hash published in the report index / site and require them with:

```sh
python3 -B verify.py editions/2026-10 --expect 2026-10=N:<sha256>
```

Replace N and `<sha256>` with the published values. Repeat `--expect` to pin
multiple editions when checking a kit/repository root. Any revision/hash mismatch
or requested edition that is not checked fails. Each successor hashes its
predecessor and its errata document, so one tip pin covers the entire chain.

Add `--require-no-skips` to fail on every skipped check, with each check named
as FAIL and a non-zero exit. It also applies to every SKIP row from `--rejudge`.
Without the flag, SKIPs remain unverified and do not change the exit code when
no checks fail.

For a verifier-independent check, run `shasum -a 256 MANIFEST.vN.json` inside the
edition directory (`shasum -a 256 MANIFEST.json` for N=0) and compare its output
with the value published in the report index / site. Hashes detect changes
relative to that independently obtained value; they do not authenticate the
publisher.

Run the offline suite in the repository checkout with
`python3 -B -m unittest discover -s control -p 'test*.py' -v`.
The synthetic errata tests cover missing/tampered/omitted files, manifest lineage,
schema and path refusals, publication scans, preservation, and both edition CLI
paths. Site order/omission tests, report index links, and corrected dated PDFs
belong to the separate site implementation.

## Dated corrections — 2026-10-15

Both editions now have revision 1 with eleven entries each. Labels such as
`2026-07-C01` occur at the start of an entry's `claim` field; `C01` is entry 1,
and so on. The reports, original manifests, metrics and run bundles retain
their original bytes. Read these corrections before interpreting the findings.

The table records the historical claim corrections and verification limits.
It does not certify deferred capture studies,
cross-harness controls, prospective study enforcement, exported judge controls,
strict no-SKIP verification, or independent rejudge receipts. The reports are
historical case studies with errata.

| Claim | July entry (`2026-07-…`) | October entry (`2026-10-…`) | Correction |
| --- | --- | --- | --- |
| Usage capture | C01 | C01 | Accounting consistency is not independently validated capture completeness or parity; error direction is qualified |
| Thinking tokens | C02 | C02 | Recorded output is priced; historical thinking inclusion is unvalidated |
| Configuration | C03 | C03 | Model-tool configuration, effort-recording limits, isolation and cache policy |
| Cost ordering | C04 | C04 | Edition 1 Opus/Fable ranges overlap; its inversion cannot be reused as stable ordering evidence; N=3 and independence limits apply |
| Calibration | C05 | C05 | Calibration failures reused for admission and scoring |
| Study history | C06 | C06 | Each edition's deviations are disclosed; October replication, admission and repricing were post-result amendments |
| Task exposure | C07 | C07 | Previously public tasks are not demonstrably unseen; tests are operator-written |
| Cost spread | — (no 499× claim) | C08 | Rate × token volume × token mix decomposition |
| TIER measure | C08 | C08 | Benchmark TIER differs from the product's merged-work measure |
| Reliability | C09 | C09 | Leading-indicator hypothesis, independence limits and unmeasured variance direction; October first-call cache evidence is not backdated to July |
| Verification | C10 | C10 | Default checks execute no submissions; SKIP can exit 0; no third-party receipt or reproduction claim |
| Custody and timing | C11 | C11 | Pre-redaction hashes and historical timing cannot be proved by the kit |

Every repository evidence link identifies a file in public kit commit
`60489d2a862b44f88ff5b2038ed02566ff139934`; the thinking-billing reference is
Anthropic's public documentation. No local assessment or private receipt is
cited as public evidence. In particular, the broader thinking-token survey is
not shipped, so October C02 makes the narrower public-evidence qualification
rather than publishing that survey as a reproducible measurement. October C03
retains the accepted configuration wording and separately explains the early
stamp format using public stamps. October C09 defines M14 as first-call cache
evidence and retains the accepted ordering and variance wording. July does not
inherit October's measured effort counts, cache shares or first-call findings.

Pins for the report index and site to publish outside the kit:

| Edition | Errata revision | Newest-manifest SHA-256 |
| --- | --- | --- |
| 2026-07 | 1 | `ec6197406c9a11cfccc9b28b5e9fb0d2ee2f732c1a4c9a844f95b3957aaacf71` |
| 2026-10 | 1 | `cbf05c8967343f3fb38b0fb2625c3b804c27d3bc12fbaa4dca14ee6c84538539` |

These values identify the generated `MANIFEST.v1.json` files. Their publication
here supplies the handoff values; freshness still requires obtaining the pins
outside the downloaded kit as described above.


## Dated corrections — 2026-10-16

Revision 2 appends C12–C15 to each edition through `errata.py`. All original
edition artifacts and revision 1 sidecars retain their published bytes.
C12 supplements the REPORT Method and configuration sections; October uses
the accepted configuration wording and its corrected early-stamp rider verbatim.
C13 corrects the REPORT admission-rule and cross-edition comparison sections,
disclosing calibration-selected haiku failures and limiting the successor
comparison. C14 corrects the Results, cross-edition comparison and Reliability
sections. It applies the accepted ordering and cache qualification
to October: independence cannot be assumed and the direction of variance error
was not measured. July receives the conditional small-sample ordering limit,
without backdating October's cache evidence or effort measurements.

C15 corrects the REPORT Stamp, verification instructions and Evidence preparation
explanation with the current measured receipts in
`REJUDGE-RECEIPT.json`: July reproduced 63 verdicts (60 accepted, 3 rejected),
and October reproduced 30 accepted verdicts, with zero SKIPs and exit 0 for
both strict rejudge commands. These are maintainer measurements inside an
exported `--edition all` kit, using the workflow commands recorded in the receipt,
without OS network isolation. The July mirror was byte-checked against the root
bundles and its 63 outcomes were rejudged once. Only the Docker launcher provides
network isolation; a bare-host run executes model-written code with the user's
account and network. The kit ships `rejudge-networkless.sh` and
`rejudge_workflow.py` for a network-disabled run of all 93 outcomes and a
flipped-verdict negative control. No third-party clean-rejudge receipt has been
established. The workflow does not establish judge validity or production
quality and does not re-run models.

Current handoff pins for publication outside the downloaded kit:

| Edition | Errata revision | Newest-manifest SHA-256 |
| --- | --- | --- |
| 2026-07 | 2 | `9981ab9e6e736a72e18f1318688a92e51f30cd687f13cacfb9d1eb768efebc42` |
| 2026-10 | 2 | `48974d598739785904d59938cbf4ba10e6a44af1aba2b8249145d6ed417e3e7f` |

The earlier dated pins remain historical. Use these revision 2 values with
`verify.py --expect` after obtaining them independently of the downloaded kit;
a self-consistent chain alone cannot prove freshness.
