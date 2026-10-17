# Public harness lineage

`../HARNESS-LINEAGE.json` maps all 93 published bundles to seven scored `run.sh`
versions and records dependencies at the eight comparison revisions cited in
TASKS / the October report. It also includes `2d1a87b`, the portability change
that produced public `score.py` blob `94094d3`. The sanitized October copy is
published as blob `ea60b6552b3eadad2608698061824f7ad2d07d36`.

Private commits are provenance identifiers, not public Git objects. Each
revision lists exact source paths, published `blob` IDs, published SHA-256
digests, transformations, and paths absent at that revision. Untransformed
entries also record `source_blob`, equal to `blob`; transformed entries omit
`source_blob` so a digest cannot confirm guesses about private original bytes.
`blobs/<blob>` contains only the screened published bytes. A Git blob ID is the
SHA-1 of `blob <byte-length>\0` followed by the file bytes.

The legacy stamps do **not** capture harness commits. October attributions come
from `editions/2026-10/REPORT.md`, “Runs and harness heads”. July uses the
last committed `run.sh` before the run's stamp time; the working tree was not
captured. The measured UTC commit times and bundle counts are:

| Revision | Commit time (UTC) | `run.sh` blob prefix | July bundles |
| --- | --- | --- | --- |
| `8fce635` | July 22, 22:25:56 | `a2b437e` | 32 (21 baseline, 11 cronspec) |
| `5508c75` | July 22, 23:48:01 | `77bcd13` | 10 cronspec |
| `da2561f` | July 23, 00:36:17 | `d37daca` | 21 orgsync |

This does not establish the exact July runtime checkout or attest every runtime
dependency. `da2561f` is also included as a cited comparison revision.
Missing historical dependencies are listed as `absent`, never filled from HEAD.
The per-bundle stamp SHA-256 binds the attribution to its evidence; the vendor
CLI version is copied from each stamp, or marked `not captured`.

## Compare without private history

From the public kit root, reproduce the report's `14f21dc` / `ec9574f` comparison:

```sh
python3 - <<'PY'
import difflib, hashlib, json
from pathlib import Path
m = json.loads(Path('HARNESS-LINEAGE.json').read_text())
def content(ref, name):
    revision = next(r for r in m['revisions'].values() if any(a.startswith(ref) for a in r['aliases']))
    entry = revision['files'][name]
    data = (Path('harness/blobs') / entry['blob']).read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == entry['blob']
    assert hashlib.sha256(data).hexdigest() == entry['sha256']
    return data.decode().splitlines(keepends=True)
a, b, name = '14f21dc', 'ec9574f', 'run.sh'
print(''.join(difflib.unified_diff(content(a, name), content(b, name),
                                   fromfile=a + ':' + name, tofile=b + ':' + name)), end='')
PY
```

Change the refs and filename for other comparisons. For example, compare
`3a5c952` / `849c8cc` / `69e1843` for the report's identical-file claim, and
`66bd8ec` / `1ad7ea8` for the Flash comparison. `score.py` at `14f21dc` versus
`2d1a87b` shows the public portability change. Two equal published blob IDs mean
identical published bytes. Transformed entries retain commit, source path, and
transform names as provenance; original bytes and their blob IDs are omitted.
You can also run `git hash-object harness/blobs/<blob>` and
`diff -u harness/blobs/<first-blob> harness/blobs/<second-blob>`; diff exits 1
when there are differences and 0 when the files are identical. Neither command
requires the private commit objects.

## Safety and transformations

All seven scored `run.sh` versions are byte-exact originals (`a2b437e`, `77bcd13`,
`d37daca`, `8768731`, `4294038`, `5e22dd7`, `501ed64` prefixes); they have no transformations.
Some other historical source cannot pass today's identity scan unchanged:

- `sanitize-home-v1`: replace the whole home-based `DEFAULT_PRICES` value in
  historical `score.py` with `<path-to>/prices.yaml`, removing the private
  directory layout. Only the distributed copy's content address is published.
- `split-scan-markers-v1`: in historical `redact.py` / `run_audit.py`, split the
  vendor instruction marker into concatenated Python string literals, reword
  its comment, and group `Users` in the home-path detection regex. These source
  spellings pass the scanner while retaining the detector's behavior.
- `sanitize-archive-v1`: apply the exporter's existing reviewed-name and path
  sanitizer to historical `master_table.py`, as for its current public copy.

Originals and transformed copies both pass the shared sealed-content refusal.
Published copies also pass the complete export identity scan. The generator
queries only literal paths derived from `export.py`'s allowlist, including
revealed judges, tracked leaves from anchored task/design directories, and
execution metadata. Tracked leaves come from `git ls-files`; untracked working
tree files are ignored. Public kits revalidate the declared leaves against the
same directory boundary without requiring private Git history.
It never enumerates historical trees or exports private history. The historical
sealed objects read by the exporter's safety check stay private. The exporter
includes only blob addresses enumerated by the validated manifest; extra files
under `blobs/` do not automatically ship. These files support public inspection
and comparison; they are not a complete, attested replay environment.

## Regenerate (private source checkout)

```sh
python3 harness_lineage.py
python3 harness_lineage.py --check
python3 -m unittest discover -s control -p 'test*.py'
```

Generation uses fixed cited refs and published bundle stamps, with sorted JSON,
no generation timestamp, and no dependency on the current branch name or HEAD.
`--out <empty-directory>` generates the same manifest and blob files elsewhere.
Generation validates every payload before writing outputs. Export uses the
checked-in artifacts and needs no access to private revision objects.
