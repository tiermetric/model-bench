#!/usr/bin/env python3
"""Aggregate per-run results into per-cell statistics for an edition.

A CELL is one (tool, model, task) pair; a RUN is one replicate of a cell.
run.sh writes <tool>/<model>/<task>/rep<k>/result.json; this reads all of them
and emits the edition table.

THE HEADLINE METRIC (PROTOCOL §4.1):

    expected cost per accepted outcome = (total spend over ALL runs)
                                       / (number of PASSING runs)

Not the mean over passing runs. That older definition is survivorship-biased:
it charges a model nothing for its failures, so an unreliable-but-cheap model
outranks a dearer reliable one even when obtaining one accepted outcome costs
strictly more. Worked example -- luna at $0.10 passing 1/3 vs sonnet at $0.26
passing 3/3: mean-over-passing says luna wins at $0.10; the correct expected
spend per accepted outcome is $0.30 vs $0.26, and sonnet is genuinely cheaper.

The mean over passing runs is still reported, clearly labelled, as "cost when
it works". A cell with zero passing runs has NO cost-efficiency figure at all --
cost without an accepted outcome is not efficiency, it is just spend -- so the
total spend is reported instead and the ratio is left undefined. Never
extrapolate it.

Usage:  python3 aggregate.py [root]  [--json out.json]
"""
import json
import os
import sys
import glob
from collections import defaultdict

# A saturation/over-hard verdict is only meaningful against the full declared
# roster. Below this, the panel is calibration data and no verdict is rendered.
# Edition 1's roster was 7; 5 is the floor at which "all but at most one" still
# means something stronger than "most of a handful".
MIN_PANEL_FOR_VERDICT = 5


def load_runs(root):
    runs = []
    for path in sorted(glob.glob(os.path.join(root, "*/*/*/rep*/result.json"))):
        try:
            d = json.load(open(path))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN: unreadable {path}: {exc}", file=sys.stderr)
            continue
        d["_path"] = os.path.relpath(path, root)
        runs.append(d)
    return runs


def cell_stats(runs):
    """One cell's statistics. `runs` is every replicate, passing and failing."""
    n = len(runs)
    costs = [r.get("cost_list_usd") for r in runs]
    priced = [c for c in costs if c is not None]
    passes = [r for r in runs if r.get("outcome") == "PASS"]
    n_pass = len(passes)

    # Total spend counts EVERY run -- failures cost real money and the metric
    # must reflect that. Unpriced runs (capture failures) are counted in
    # `unpriced` and flagged, never silently treated as $0.
    total_spend = sum(priced)
    unpriced = len(costs) - len(priced)

    pass_costs = [r["cost_list_usd"] for r in passes
                  if r.get("cost_list_usd") is not None]

    # Cost dispersion within a cell. This is not decoration: it is the error bar
    # on the number the report publishes, and at N=1 it cannot be computed at
    # all -- a single run gives a point estimate with no way to know whether it
    # is typical. It also behaves like a leading indicator of the failure edge.
    # Measured in Edition 1: mean within-cell variation was 4.0% on the EASY
    # task and 8.4% on the MEDIUM one, but haiku on cronspec ranged 73%
    # ($0.3253-$0.5640) while every other model on that task sat at 5-11%.
    # Haiku passed cronspec 3/3 -- the dispersion showed it was working near the
    # edge of its competence before any failure appeared. A model that thrashes
    # explores, backtracks and retries, and that shows up in cost long before it
    # shows up in pass/fail.
    disp = None
    if len(priced) > 1:
        mean = sum(priced) / len(priced)
        var = sum((c - mean) ** 2 for c in priced) / len(priced)
        disp = round(100.0 * (var ** 0.5) / mean, 1) if mean else None

    return {
        "n": n,
        "n_pass": n_pass,
        "pass_rate": n_pass / n if n else None,
        "total_spend_usd": round(total_spend, 6),
        "expected_cost_per_accepted_usd":
            round(total_spend / n_pass, 6) if n_pass else None,
        "mean_cost_when_passing_usd":
            round(sum(pass_costs) / len(pass_costs), 6) if pass_costs else None,
        "cost_dispersion_pct": disp,
        "cost_min_usd": round(min(priced), 6) if priced else None,
        "cost_max_usd": round(max(priced), 6) if priced else None,
        "unpriced_runs": unpriced,
        "single_observation": n_pass == 1,
        "wall_seconds": [r.get("wall_seconds") for r in runs],
        "outcomes": [r.get("outcome") for r in runs],
        "agent_exits": [r.get("agent_exit") for r in runs],
        "capture": [r.get("capture") for r in runs],
    }


def saturation_verdict(cells, task):
    """PROTOCOL §3.3 triggers, stated in MODELS not runs.

    A raw run-percentage trigger fires on noise: with 7 models x N=3, 18/21 is
    85.7%, so a single flaky run decides whether a task graduates -- and
    graduation is irreversible. It also cannot tell apart two very different
    panels that both read 18/21: six models perfect plus one total failure
    (saturated, one outlier) versus four perfect plus three splitting
    (discriminating beautifully). Model-unit rules separate them.
    """
    tc = {k: v for k, v in cells.items() if k[2] == task}
    if not tc:
        return None
    n_models = len(tc)
    perfect = sum(1 for s in tc.values() if s["n_pass"] == s["n"] and s["n"])
    any_pass = sum(1 for s in tc.values() if s["n_pass"] > 0)
    # The all-but-at-most-one rule is defined against a FULL declared roster
    # (PROTOCOL §3.3), where it means "6 or 7 of 7 were perfect". It degenerates
    # badly on a partial panel: at 3 models it collapses to "2 of 3", which
    # labelled orgsync SATURATED on the very panel where one model failed all
    # three of its runs — the most discriminating result the report had ever
    # produced. A rule that calls that saturation is measuring panel size, not
    # the task.
    #
    # A partial panel is also, by definition, calibration — and PROTOCOL §3.4
    # says calibration runs are authoring data, never published as scored. So no
    # saturation verdict is rendered here at all; the discrimination shape is
    # reported instead, which is what a calibration decision actually needs.
    # A verdict also requires UNIFORM N. A ladder mixes N=3 cells with N=1
    # confirmation runs, and a rule computed over that is measuring run order,
    # not the task: adding one N=1 pass flipped orgsync — the only task that has
    # ever produced a failure — from "no verdict" to SATURATED. An edition panel
    # is a declared roster run at uniform N; anything else is calibration.
    ns = sorted({s["n"] for s in tc.values()})
    if len(ns) > 1:
        return {"task": task, "verdict": "CALIBRATION (no verdict)",
                "why": (f"mixed replication across cells (N={ns}) — a verdict needs a "
                        f"declared roster at uniform N; this is a ladder, not an "
                        f"edition panel. Shape: {perfect}/{n_models} models perfect, "
                        f"{n_models - any_pass}/{n_models} failed every run"),
                "models": n_models, "perfect": perfect, "any_pass": any_pass}
    if n_models < MIN_PANEL_FOR_VERDICT:
        shape = (f"{perfect}/{n_models} models perfect, "
                 f"{n_models - any_pass}/{n_models} failed every run")
        return {"task": task, "verdict": "CALIBRATION (no verdict)",
                "why": (f"{n_models} models is a partial panel; the saturation rule "
                        f"needs the full declared roster (>={MIN_PANEL_FOR_VERDICT}). "
                        f"Discrimination shape: {shape}"),
                "models": n_models, "perfect": perfect, "any_pass": any_pass}
    if perfect >= n_models - 1:
        v = "SATURATED"
        why = f"{perfect}/{n_models} models passed every run (all-but-at-most-one)"
    elif any_pass < n_models / 2:
        v = "OVER-HARD"
        why = (f"only {any_pass}/{n_models} models produced any passing run "
               "-- too few cells to draw an efficiency frontier")
    else:
        v = "IN-BAND"
        why = (f"{perfect}/{n_models} models perfect, "
               f"{any_pass}/{n_models} with at least one pass")
    return {"task": task, "verdict": v, "why": why,
            "models": n_models, "perfect": perfect, "any_pass": any_pass}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = args[0] if args else os.path.dirname(os.path.abspath(__file__))
    out_json = None
    if "--json" in sys.argv:
        out_json = sys.argv[sys.argv.index("--json") + 1]

    runs = load_runs(root)
    if not runs:
        print("no per-run results found (expected <tool>/<model>/<task>/rep*/result.json)",
              file=sys.stderr)
        return 1

    grouped = defaultdict(list)
    for r in runs:
        grouped[(r.get("tool"), r.get("label"), r.get("task"))].append(r)
    cells = {k: cell_stats(v) for k, v in grouped.items()}

    tasks = sorted({k[2] for k in cells})
    report = {"runs_total": len(runs), "cells": {}, "tasks": {}}

    for task in tasks:
        print(f"\n=== task: {task}")
        print(f"{'tool':12} {'model':14} {'pass':>6} {'E[$/accepted]':>14} "
              f"{'$ when works':>13} {'total $':>9} {'range':>18} {'disp':>6}")
        print("-" * 100)
        rows = sorted(((k, v) for k, v in cells.items() if k[2] == task),
                      key=lambda kv: kv[1]["expected_cost_per_accepted_usd"]
                      if kv[1]["expected_cost_per_accepted_usd"] is not None
                      else float("inf"))
        for (tool, model, _), s in rows:
            exp = s["expected_cost_per_accepted_usd"]
            mw = s["mean_cost_when_passing_usd"]
            rng = (f"${s['cost_min_usd']:.4f}-${s['cost_max_usd']:.4f}"
                   if s["cost_min_usd"] is not None else "-")
            flag = " *single-obs" if s["single_observation"] else ""
            flag += f" *{s['unpriced_runs']}-unpriced" if s["unpriced_runs"] else ""
            dp = s["cost_dispersion_pct"]
            print(f"{tool:12} {model:14} {s['n_pass']:>3}/{s['n']:<2} "
                  f"{('$%.4f' % exp) if exp is not None else 'NO ACCEPTED':>14} "
                  f"{('$%.4f' % mw) if mw is not None else '-':>13} "
                  f"${s['total_spend_usd']:>8.4f} {rng:>18} "
                  f"{('%.1f%%' % dp) if dp is not None else '-':>6}{flag}")
            report["cells"][f"{tool}/{model}/{task}"] = s

        v = saturation_verdict(cells, task)
        report["tasks"][task] = v
        print("-" * 100)
        print(f"VERDICT: {v['verdict']} -- {v['why']}")
        exps = [s["expected_cost_per_accepted_usd"] for (_, _, t), s in cells.items()
                if t == task and s["expected_cost_per_accepted_usd"]]
        if len(exps) > 1:
            print(f"efficiency spread: {max(exps)/min(exps):.1f}x "
                  f"(${min(exps):.4f} -> ${max(exps):.4f} per accepted outcome)")

    if out_json:
        json.dump(report, open(out_json, "w"), indent=2)
        print(f"\nwrote {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
