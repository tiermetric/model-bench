#!/usr/bin/env python3
"""Produce the PUBLISHABLE form of an edition's spend evidence.

A raw session log is the honest source for a cost figure, and it is also a full
transcript: prompts, model reasoning, tool output, file diffs, the operator's
home-directory paths, and — for Codex — the vendor's proprietary system prompt.
Edition 1's 45 logs carry 2,892 home-path occurrences and 18 copies of that
system prompt. Publishing them to satisfy verifiability would disclose all of it.

That is a false choice. The cost arithmetic only ever depended on the usage
counters, so this emits a log containing exactly those and nothing else, plus
the SHA-256 of the original. `verify.py` re-derives every published number from
the redacted form offline and unchanged — which is the whole verifiability
claim — while the transcript never leaves the machine.

What a reader gives up, stated honestly: they cannot confirm the ORIGINAL log
hashes to what we published without holding the original. They can still check
that our numbers follow from the evidence we shipped, that the evidence is
internally consistent, and that we did not retype it. That is the claim
PROTOCOL §8 makes, and it survives intact.

    python3 redact.py [root] [--out DIR] [--check]

--check verifies an already-redacted tree instead of producing one: it is the
control arm, and it must FAIL on an unredacted log.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

# Fields the cost derives from. Anything not named here is dropped, so the
# default is exclusion — a new transcript field in a future CLI version cannot
# silently start flowing through.
CLAUDE_USAGE = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")
CODEX_USAGE = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
               "output_tokens", "reasoning_output_tokens", "total_tokens")

# Markers a redacted log must never contain. Home paths identify the operator;
# base_instructions is a vendor's proprietary prompt; the message/content keys
# carry the transcript itself.
BANNED = (
    (re.compile(r"/Users/[^/\"\\ ]+"), "home-directory path"),
    (re.compile(r"base_instructions"), "vendor system prompt"),
    (re.compile(r'"(text|content|arguments|patch)"\s*:'), "transcript content"),
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def redact_claude(line, obj):
    """Keep one assistant message's identity and usage. Drop its content."""
    m = obj.get("message")
    if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
        return None
    u = m["usage"]
    keep = {k: u[k] for k in CLAUDE_USAGE if k in u}
    cc = u.get("cache_creation")
    if isinstance(cc, dict):
        keep["cache_creation"] = {
            k: v for k, v in cc.items()
            if k in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
        }
    out = {"type": obj.get("type"),
           "message": {"id": m.get("id"), "model": m.get("model"), "usage": keep}}
    if obj.get("timestamp"):
        out["timestamp"] = obj["timestamp"]
    return out


def redact_codex(line, obj):
    """Keep session identity, model, and the cumulative usage snapshots.

    Deliberately drops session_meta.cwd: it is the single largest source of
    home-path leakage and the published cost does not depend on it (attribution
    happens at capture time, not at verification time).
    """
    t = obj.get("type")
    p = obj.get("payload") or {}
    if t == "session_meta" and isinstance(p, dict):
        return {"type": t, "timestamp": obj.get("timestamp"),
                "payload": {"id": p.get("id"), "cli_version": p.get("cli_version")}}
    if t == "turn_context" and isinstance(p, dict) and p.get("model"):
        return {"type": t, "payload": {"model": p.get("model")}}
    if isinstance(p, dict) and p.get("type") == "token_count":
        info = p.get("info") or {}
        tt = info.get("total_token_usage")
        if not isinstance(tt, dict):
            return None
        return {"type": t, "timestamp": obj.get("timestamp"),
                "payload": {"type": "token_count", "info": {
                    "total_token_usage": {k: tt[k] for k in CODEX_USAGE if k in tt}}}}
    return None


def redact_file(src, dst):
    fmt = "codex" if _is_codex(src) else "claude"
    fn = redact_codex if fmt == "codex" else redact_claude
    kept = 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w") as out:
        for line in open(src, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            r = fn(line, obj)
            if r is not None:
                out.write(json.dumps(r, sort_keys=True) + "\n")
                kept += 1
    return fmt, kept


def _is_codex(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"token_count"' in line or '"session_meta"' in line:
                return True
            if '"message"' in line:
                return False
    return False


def scan_banned(path):
    """Return every banned marker found. This is the control arm."""
    hits = []
    text = open(path, encoding="utf-8", errors="replace").read()
    for pattern, label in BANNED:
        n = len(pattern.findall(text))
        if n:
            hits.append((label, n))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--out", default=None, help="output tree (default <root>/publish)")
    ap.add_argument("--check", action="store_true",
                    help="verify a tree is clean instead of redacting; exits non-zero on any leak")
    args = ap.parse_args()

    if args.check:
        target = args.out or os.path.join(args.root, "publish")
        logs = sorted(glob.glob(os.path.join(target, "**", "session.jsonl"), recursive=True))
        if not logs:
            print(f"no redacted logs under {target}", file=sys.stderr)
            return 2
        bad = 0
        for p in logs:
            hits = scan_banned(p)
            if hits:
                bad += 1
                print(f"  LEAK {os.path.relpath(p, target)}: "
                      + ", ".join(f"{n}x {label}" for label, n in hits))
        print(f"\nchecked {len(logs)} redacted logs — {bad} with leaks")
        if bad:
            print("REDACTION FAILED — these must not be published.")
            return 1
        print("CLEAN — no home paths, no vendor prompt, no transcript content.")
        return 0

    out_root = args.out or os.path.join(args.root, "publish")
    srcs = sorted(glob.glob(os.path.join(args.root, "*/*/*/rep*/session.jsonl")))
    if not srcs:
        print("no session logs found", file=sys.stderr)
        return 2

    manifest, before, after = [], 0, 0
    for src in srcs:
        rel = os.path.relpath(src, args.root)
        dst = os.path.join(out_root, rel)
        digest = sha256_file(src)
        fmt, kept = redact_file(src, dst)
        before += os.path.getsize(src)
        after += os.path.getsize(dst)

        # The rest of the bundle has to travel too, or the published evidence
        # cannot be verified — verify.py needs the result, the stamp, and the
        # rate table the cost was computed against.
        sdir, ddir = os.path.dirname(src), os.path.dirname(dst)
        for name in ("stamp.json", "prices.snapshot.yaml"):
            if os.path.exists(os.path.join(sdir, name)):
                with open(os.path.join(sdir, name), "rb") as a, \
                     open(os.path.join(ddir, name), "wb") as b:
                    b.write(a.read())
        # result.json records the session log by ABSOLUTE path — the operator's
        # home directory, in every bundle. Rewrite to the relative name that is
        # true of the published tree.
        rp = os.path.join(sdir, "result.json")
        if os.path.exists(rp):
            res = json.load(open(rp))
            for k, v in list(res.items()):
                if isinstance(v, str) and v.startswith("/"):
                    res[k] = os.path.basename(v)
            json.dump(res, open(os.path.join(ddir, "result.json"), "w"),
                      indent=2, sort_keys=True)
        # The integrity hash must describe the log that actually ships, or the
        # published tree fails its own check.
        with open(os.path.join(ddir, "session.jsonl.sha256"), "w") as fh:
            fh.write(sha256_file(dst) + "\n")

        manifest.append({"bundle": os.path.dirname(rel), "format": fmt,
                         "original_sha256": digest,
                         "original_bytes": os.path.getsize(src),
                         "redacted_sha256": sha256_file(dst),
                         "redacted_bytes": os.path.getsize(dst),
                         "records_kept": kept})
    mpath = os.path.join(out_root, "EVIDENCE-MANIFEST.jsonl")
    with open(mpath, "w") as fh:
        for row in manifest:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    print(f"redacted {len(srcs)} session logs -> {out_root}")
    print(f"  {before/1e6:.2f} MB original -> {after/1e6:.2f} MB publishable "
          f"({100*after/max(before,1):.1f}%)")
    print(f"  manifest: {os.path.relpath(mpath, args.root)} "
          f"(carries each original's SHA-256)")
    print(f"\nNow run:  python3 redact.py --check      # the control arm")
    print(f"          python3 verify.py {out_root}   # numbers must still re-derive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
