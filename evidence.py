#!/usr/bin/env python3
"""Content-addressed evidence store: every artifact a run was judged against.

THE GAP THIS CLOSES

Every bundle records WHICH prompt, judge and rate table produced its number --
`stamp.json` carries `prompt_file_sha256`, `judge_sha256`, `prices_yaml_sha256`.
It does not carry the artifacts themselves. So a bundle is verifiable only by
someone who also holds this repository at the exact commit the run was made
from. Publish the bundles alone and the hashes point at nothing.

That is not a theoretical gap. The whole point of the task ladder is that
prompts and judges are REVISED between generations, and PROTOCOL requires the
superseded versions stay readable forever so a reader can see what changed and
when. A file that is edited in place destroys its own predecessor.

THE STORE

    evidence/prompts/<sha256>.md      the exact prompt text a run was given
    evidence/judges/<sha256>.py       the exact judge that accepted or rejected it
    evidence/prices/<sha256>.yaml     the exact rate table its cost was computed from
    evidence/INDEX.jsonl              sha -> {kind, current name, first/last seen, runs}

Addressing by content rather than by name gives four properties at once:
archival (a superseded prompt keeps its own address and is never overwritten),
dedup (61 bundles sharing one prompt store one copy), integrity (the filename IS
the checksum -- corruption cannot hide), and self-verification (a published
bundle plus this store needs nothing else).

The join already exists: stamp.json's three sha fields ARE the keys. Nothing in
the run pipeline had to change to make this work.

    python3 evidence.py            # build/refresh the store from every bundle
    python3 evidence.py --check    # verify: re-hash content, prove every run resolves

--check is the control arm and must FAIL on a corrupted or missing artifact.
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict

# stamp field -> (subdirectory, source glob relative to root, extension)
#
# Judges are per-task files at the repo ROOT -- acceptance_<task>.py, with
# baseline keeping the historical acceptance_test.py name (run.sh:40-55). They
# are NOT in a judges/ directory, and a lookup that assumes one silently
# resolves nothing while reporting success on prompts.
KINDS = {
    "prompt_file_sha256": ("prompts", "prompts/*.md", ".md"),
    "judge_sha256": ("judges", "acceptance_*.py", ".py"),
    "prices_yaml_sha256": ("prices", None, ".yaml"),  # snapshot lives in the bundle
}


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_source(root, stamp, field, bundle):
    """Locate the artifact this stamp field refers to.

    Prompts and judges live in the repo under their CURRENT name; the price
    table was snapshotted into the bundle at run time, which is why a rate
    change after the fact cannot silently rewrite history.
    """
    sub, srcglob, ext = KINDS[field]
    want = stamp.get(field)
    if not want:
        return None, None
    if field == "prices_yaml_sha256":
        p = os.path.join(bundle, "prices.snapshot.yaml")
        return (p, want) if os.path.exists(p) else (None, want)
    # Try the exact name the stamp recorded first, then every candidate of that
    # kind anywhere in the tree. Content decides, not the name: a task's judge
    # or prompt may be renamed between generations while its bytes -- and so its
    # address in this store -- do not change.
    name = stamp.get("prompt" if field == "prompt_file_sha256" else "judge")
    cands = []
    if name:
        cands.append(os.path.join(root, os.path.dirname(srcglob), name))
        cands.append(os.path.join(root, os.path.dirname(srcglob), name + ext))
    cands += sorted(glob.glob(os.path.join(root, srcglob)))
    cands += sorted(glob.glob(os.path.join(root, "archive", "**",
                                           os.path.basename(srcglob)),
                              recursive=True))
    for c in cands:
        if os.path.isfile(c) and sha256_file(c) == want:
            return c, want
    return None, want


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?",
                    default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--check", action="store_true",
                    help="verify an existing store instead of building it")
    args = ap.parse_args()

    root = args.root
    store = os.path.join(root, "evidence")
    stamps = sorted(glob.glob(os.path.join(root, "*/*/*/rep*/stamp.json")))
    if not stamps:
        print("no bundles found", file=sys.stderr)
        return 2

    # sha -> record
    index = {}
    unresolved = []
    for sp in stamps:
        bundle = os.path.dirname(sp)
        rel = os.path.relpath(bundle, root)
        try:
            stamp = json.load(open(sp))
        except (OSError, json.JSONDecodeError) as e:
            print(f"WARN unreadable {sp}: {e}", file=sys.stderr)
            continue
        for field in KINDS:
            src, want = find_source(root, stamp, field, bundle)
            if not want:
                continue
            if src is None:
                unresolved.append((rel, field, want))
                continue
            rec = index.setdefault(want, {
                "sha256": want, "kind": KINDS[field][0],
                "names": set(), "runs": [], "_src": src})
            nm = stamp.get("prompt" if field == "prompt_file_sha256" else
                           "judge" if field == "judge_sha256" else "prices")
            if nm:
                rec["names"].add(nm)
            rec["runs"].append(rel)

    if args.check:
        bad = 0
        for sha, rec in sorted(index.items()):
            p = os.path.join(store, rec["kind"], sha + _ext(rec["kind"]))
            if not os.path.exists(p):
                print(f"  MISSING {rec['kind']}/{sha[:12]} "
                      f"({len(rec['runs'])} runs reference it)")
                bad += 1
                continue
            actual = sha256_file(p)
            if actual != sha:
                print(f"  CORRUPT {rec['kind']}/{sha[:12]} -> hashes to {actual[:12]}")
                bad += 1
        for rel, field, want in unresolved:
            print(f"  UNRESOLVED {rel} {field}={want[:12]} — artifact not found anywhere")
            bad += 1
        n_runs = len({r for rec in index.values() for r in rec["runs"]})
        print(f"\nchecked {len(index)} artifacts across {n_runs} bundles — "
              f"{bad} problem(s)")
        if bad:
            print("EVIDENCE INCOMPLETE — these runs cannot be verified from the store.")
            return 1
        print("COMPLETE — every run resolves to stored prompt, judge and rate table.")
        return 0

    written = 0
    for sha, rec in sorted(index.items()):
        d = os.path.join(store, rec["kind"])
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, sha + _ext(rec["kind"]))
        if not os.path.exists(dst):
            shutil.copyfile(rec["_src"], dst)
            written += 1
        # Re-hash what we actually wrote. Trusting the copy is how a silent
        # truncation becomes permanent evidence.
        got = sha256_file(dst)
        if got != sha:
            print(f"FATAL: wrote {dst} but it hashes to {got}", file=sys.stderr)
            return 1

    idx = os.path.join(store, "INDEX.jsonl")
    with open(idx, "w") as fh:
        for sha, rec in sorted(index.items(), key=lambda kv: (kv[1]["kind"], kv[0])):
            fh.write(json.dumps({
                "sha256": sha,
                "kind": rec["kind"],
                "names": sorted(rec["names"]),
                "path": f"evidence/{rec['kind']}/{sha}{_ext(rec['kind'])}",
                "run_count": len(rec["runs"]),
                "runs": sorted(rec["runs"]),
            }, sort_keys=True) + "\n")

    by_kind = defaultdict(int)
    for rec in index.values():
        by_kind[rec["kind"]] += 1
    print(f"evidence store: {len(index)} unique artifacts ({written} newly stored)")
    for k in sorted(by_kind):
        print(f"  {k:8} {by_kind[k]:>3} distinct version(s)")
    print(f"  index   {os.path.relpath(idx, root)}")
    if unresolved:
        print(f"\n{len(unresolved)} UNRESOLVED reference(s) — a run points at an "
              f"artifact that exists nowhere in the tree:")
        for rel, field, want in unresolved[:10]:
            print(f"  {rel} {field}={want[:12]}")
        return 1
    print("\nNow run:  python3 evidence.py --check    # the control arm")
    return 0


def _ext(kind):
    return {"prompts": ".md", "judges": ".py", "prices": ".yaml"}[kind]


if __name__ == "__main__":
    sys.exit(main())
