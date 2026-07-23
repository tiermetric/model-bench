#!/usr/bin/env python3
"""Flatten an edition's run bundles into machine-readable datasets.

Produces two files, both stable-schema and safe to append to across editions:

  results.jsonl  -- ONE LINE PER RUN. The atomic record. Never per-cell:
                    aggregation is a group-by the reader performs, so nobody
                    has to trust our summary. This is also what makes a
                    cherry-picked edition detectable -- the runs are all here.
  results.csv    -- the same rows, for spreadsheets and quick plotting.

Why per-run and not per-cell: a cell figure is a claim; a run is evidence. Every
number in a published edition must be recomputable from these rows plus the
archived session logs, which is exactly what verify.py checks.

Usage:  python3 export.py [root] [--edition NAME]
"""
import csv
import json
import os
import sys
import glob

FIELDS = [
    "edition", "task", "tool", "model", "model_key", "rep",
    "outcome", "acceptance", "own_tests", "edge_failures",
    "cost_list_usd", "priced",
    "input_uncached", "cache_read", "cache_write", "output", "reasoning",
    "wall_seconds", "agent_exit", "timed_out", "capture",
    "date_utc", "tier_version", "tool_version",
    "prompt", "prompt_file_sha256", "judge", "judge_sha256",
    "prices_yaml_sha256", "python_version", "machine", "bundle",
]


def rows(root, edition):
    for p in sorted(glob.glob(os.path.join(root, "*/*/*/rep*/result.json"))):
        d = os.path.dirname(p)
        try:
            res = json.load(open(p))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN: unreadable {p}: {exc}", file=sys.stderr)
            continue
        sp = os.path.join(d, "stamp.json")
        st = json.load(open(sp)) if os.path.exists(sp) else {}
        tok = res.get("tokens") or {}
        yield {
            "edition": edition,
            "task": res.get("task") or st.get("prompt"),
            "tool": res.get("tool"),
            "model": res.get("label"),
            "model_key": res.get("model_key") or st.get("model_flag"),
            "rep": res.get("rep"),
            # outcome is the scored fact; acceptance/own_tests are its inputs.
            # own_tests is diagnostic only -- own PASS + judge FAIL is the
            # "confident failure" signal, and it is only visible if we keep both.
            "outcome": res.get("outcome"),
            "acceptance": res.get("acceptance"),
            "own_tests": res.get("own_tests"),
            "edge_failures": res.get("edge_failures"),
            "cost_list_usd": res.get("cost_list_usd"),
            "priced": res.get("priced"),
            "input_uncached": tok.get("input_uncached"),
            "cache_read": tok.get("cache_read"),
            "cache_write": tok.get("cache_write"),
            "output": tok.get("output"),
            "reasoning": tok.get("reasoning"),
            "wall_seconds": res.get("wall_seconds"),
            "agent_exit": res.get("agent_exit"),
            "timed_out": res.get("timed_out"),
            "capture": res.get("capture"),
            "date_utc": st.get("date_utc"),
            "tier_version": st.get("tier_version"),
            "tool_version": st.get("tool_version"),
            "prompt": st.get("prompt"),
            "prompt_file_sha256": st.get("prompt_file_sha256"),
            "judge": st.get("judge"),
            "judge_sha256": st.get("judge_sha256"),
            "prices_yaml_sha256": st.get("prices_yaml_sha256"),
            "python_version": st.get("python_version"),
            "machine": st.get("machine"),
            # Relative, never absolute: the bundle must be findable after the
            # archive is cloned somewhere else, on someone else's machine.
            "bundle": os.path.relpath(d, root),
        }


def main():
    argv = sys.argv[1:]
    edition = "unnamed"
    if "--edition" in argv:
        i = argv.index("--edition")
        edition = argv[i + 1]
        # Drop BOTH the flag and its value: leaving the value behind made it
        # get read as the root path, and the export silently found no bundles.
        argv = argv[:i] + argv[i + 2:]
    positional = [a for a in argv if not a.startswith("--")]
    root = positional[0] if positional else os.path.dirname(os.path.abspath(__file__))

    data = list(rows(root, edition))
    if not data:
        print("no run bundles found", file=sys.stderr)
        return 1

    jl = os.path.join(root, "results.jsonl")
    with open(jl, "w") as fh:
        for r in data:
            fh.write(json.dumps(r, sort_keys=True) + "\n")

    cv = os.path.join(root, "results.csv")
    with open(cv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(data)

    passes = sum(1 for r in data if r["outcome"] == "PASS")
    spend = sum(r["cost_list_usd"] or 0 for r in data)
    tasks = sorted({r["task"] for r in data})
    models = sorted({r["model"] for r in data})
    print(f"exported {len(data)} runs -> {jl} + {cv}")
    print(f"  edition : {edition}")
    print(f"  tasks   : {', '.join(tasks)}")
    print(f"  models  : {len(models)} ({', '.join(models)})")
    print(f"  outcomes: {passes} PASS / {len(data) - passes} not-PASS")
    print(f"  spend   : ${spend:.4f} (list rates, all runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
