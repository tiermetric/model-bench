#!/usr/bin/env python3
"""Per-run diagnostics scorer for the cross-model TIER demo (model-bench).

Reads a session log (Claude Code project JSONL, or Codex rollout JSONL),
sums token usage, prices it at LIST rates from TIER's prices.yaml, and emits
one diagnostics row (markdown + JSON).

Spend-capture method (documented decision):
  * Claude Code: parse the session's ~/.claude/projects/.../<uuid>.jsonl
    directly — sum message.usage across assistant events, DEDUPED by
    message.id (Claude Code writes one line per content block, repeating the
    same usage object; naive summing double-counts). Chosen over
    `tierd score --repo` because a fresh single-session bench dir needs no
    repo/issue attribution and direct summation has no moving parts.
  * Codex: take the LAST cumulative `token_count` event's total_token_usage
    from the rollout JSONL. Verified containments (2026-07-22 sol log):
    total == input + output; reasoning ⊆ output (do NOT add on top);
    cached_input ⊆ input (subtract for the uncached share).

Pricing:
  * claude:  $ = input*in + cache_read*in*read_mult
                 + w5m*in*1.25 + w1h*in*2.0 + output*out
    (anthropic provider defaults from prices.yaml header: read 0.1x,
     write 5m 1.25x, write 1h 2.0x; input_tokens is the UNCACHED remainder
     in Anthropic usage objects)
  * codex:   $ = (input-cached)*in + cached*in*read_mult + output*out
    (cache_write has no OpenAI SKU; assumed ⊆ input — warns if nonzero)

A model key missing from prices.yaml is a HARD ERROR unless --allow-unpriced
is passed (then the row carries cost=null and an explicit UNPRICED flag —
that gap is itself a TIER pricing-coverage finding, not something to paper
over with a guessed rate).

Control arm: total tokens must be > 0 or the run fails (no-JSONL/no-spend
runs must not produce a quiet zero row).
"""

import argparse
import json
import os
import re
import sys

DEFAULT_PRICES = os.environ.get(
    "TIER_PRICES", os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.yaml"))

# Web-tool names as they appear in each log format. Claude Code emits a
# `tool_use` (or server-side `server_tool_use`) content block named WebSearch /
# WebFetch; Codex logs a web search as a tool call named `web_search`. These are
# the only network-reaching tools a scored (offline) task may never use.
_WEB_TOOL_NAMES = {"websearch", "webfetch", "web_search", "web_fetch"}

PROVIDER_DEFAULT_READ_MULT = {
    "anthropic": 0.10,
    "openai": 0.50,   # 4-era default; 5-era rows carry explicit 0.10
}
ANTHROPIC_W5M = 1.25
ANTHROPIC_W1H = 2.00

_ROW_RE = re.compile(r'^\s*"?(?P<key>[^"#:{}]+?)"?:\s*\{(?P<body>[^}]*)\}')


def load_prices(path):
    """Minimal parser for prices.yaml's one-line flow-map model rows."""
    prices = {}
    version = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.lstrip().startswith("#"):
                continue
            vm = re.match(r"^version:\s*(\d+)", line)
            if vm:
                version = int(vm.group(1))
                continue
            m = _ROW_RE.match(line)
            if not m:
                continue
            body = m.group("body")
            fields = {}
            for part in body.split(","):
                if ":" not in part:
                    continue
                k, v = part.split(":", 1)
                fields[k.strip()] = v.strip()
            if "input_per_m" not in fields:
                continue
            prices[m.group("key").strip()] = {
                "input_per_m": float(fields["input_per_m"]),
                "output_per_m": float(fields.get("output_per_m", fields["input_per_m"])),
                "cache_read_mult": float(fields["cache_read_mult"]) if "cache_read_mult" in fields else None,
                "provider": fields.get("provider", "").strip(),
            }
    if not prices:
        raise SystemExit(f"FATAL: parsed zero model rows from {path}")
    return prices, version


def sum_claude_jsonl(path):
    """Sum usage across assistant events, deduped by message.id (last wins)."""
    by_id = {}
    models = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            mid = msg.get("id") or obj.get("uuid")
            by_id[mid] = usage
            model = msg.get("model")
            if model and model != "<synthetic>":
                models.add(model)
    tot = {"input": 0, "output": 0, "cache_read": 0, "cache_w5m": 0, "cache_w1h": 0}
    for u in by_id.values():
        tot["input"] += u.get("input_tokens", 0)
        tot["output"] += u.get("output_tokens", 0)
        tot["cache_read"] += u.get("cache_read_input_tokens", 0)
        cc = u.get("cache_creation")
        if isinstance(cc, dict):
            tot["cache_w5m"] += cc.get("ephemeral_5m_input_tokens", 0)
            tot["cache_w1h"] += cc.get("ephemeral_1h_input_tokens", 0)
        else:
            tot["cache_w5m"] += u.get("cache_creation_input_tokens", 0)
    return {
        "api_calls": len(by_id),
        "model_ids": sorted(models),
        "input_uncached": tot["input"],
        "cache_read": tot["cache_read"],
        "cache_write": tot["cache_w5m"] + tot["cache_w1h"],
        "cache_w5m": tot["cache_w5m"],
        "cache_w1h": tot["cache_w1h"],
        "output": tot["output"],
        "reasoning": None,  # Anthropic JSONL has no separate reasoning count (billed inside output)
        "input_total": tot["input"] + tot["cache_read"] + tot["cache_w5m"] + tot["cache_w1h"],
    }


def sum_codex_jsonl(path):
    """Take the LAST cumulative token_count event (total_token_usage)."""
    last = None
    models = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = re.search(r'"model":"([^"]+)"', line)
            payload = obj.get("payload") or {}
            if isinstance(payload, dict):
                mdl = payload.get("model") or (payload.get("info") or {}).get("model")
                if mdl:
                    models.add(mdl)
            if (obj.get("type") == "event_msg"
                    and isinstance(payload, dict)
                    and payload.get("type") == "token_count"
                    and (payload.get("info") or {}).get("total_token_usage")):
                last = payload["info"]["total_token_usage"]
    if last is None:
        raise SystemExit(f"FATAL: no token_count events found in {path}")
    inp = last.get("input_tokens", 0)
    cached = last.get("cached_input_tokens", 0)
    cw = last.get("cache_write_input_tokens", 0)
    out = last.get("output_tokens", 0)
    reasoning = last.get("reasoning_output_tokens", 0)
    total = last.get("total_tokens", 0)
    # Verified containments — fail loud if a future log breaks them.
    if total and total != inp + out:
        raise SystemExit(f"FATAL: codex containment broken: total={total} != input+output={inp+out}")
    if cached > inp:
        raise SystemExit(f"FATAL: codex containment broken: cached={cached} > input={inp}")
    if cw:
        print(f"WARN: cache_write_input_tokens={cw} nonzero; billed at input rate (no OpenAI write SKU)",
              file=sys.stderr)
    return {
        "api_calls": None,
        "model_ids": sorted(models),
        "input_uncached": inp - cached,
        "cache_read": cached,
        "cache_write": cw,
        "cache_w5m": 0,
        "cache_w1h": 0,
        "output": out,
        "reasoning": reasoning,
        "input_total": inp,
    }


def scan_web_tools(path, fmt):
    """Count web-tool invocations in a session log.

    Returns (count, names) where `names` is the sorted distinct set of web-tool
    identifiers seen. A scored, offline task (baseline, cronspec) must produce
    ZERO — any hit means the model reached the network and the run is not a clean
    offline measurement.

    Detection (grounded on real 2026-07-22 logs):
      * claude JSONL: assistant `content` blocks whose type is `tool_use` or
        `server_tool_use` and whose name is a web tool; plus `web_search_tool_result`
        blocks (server-side web search results).
      * codex rollout: any payload whose `type` or call `name`/`tool_name` is a
        web-search tool (codex logs tool calls as `custom_tool_call`/`function_call`
        payloads carrying a `name`).
    CAVEAT: neither reference log actually invoked a web tool, so the exact
    web-search payload shape is inferred, not observed — see model-bench notes.
    """
    hits = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if fmt == "claude":
                if obj.get("type") != "assistant":
                    continue
                msg = obj.get("message") or {}
                for b in (msg.get("content") or []):
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt in ("tool_use", "server_tool_use"):
                        if str(b.get("name", "")).lower() in _WEB_TOOL_NAMES:
                            hits.append(b.get("name"))
                    elif bt == "web_search_tool_result":
                        hits.append("web_search_tool_result")
            else:  # codex
                p = obj.get("payload")
                if not isinstance(p, dict):
                    continue
                ptype = str(p.get("type", "")).lower()
                name = str(p.get("name") or p.get("tool_name") or "").lower()
                if name in _WEB_TOOL_NAMES or "web_search" in ptype or "web_search" in name:
                    hits.append(p.get("name") or p.get("type"))
    return len(hits), sorted({str(h) for h in hits})


def price(tokens, row, fmt):
    in_rate = row["input_per_m"] / 1e6
    out_rate = row["output_per_m"] / 1e6
    read_mult = row["cache_read_mult"]
    if read_mult is None:
        read_mult = PROVIDER_DEFAULT_READ_MULT.get(row["provider"], 1.0)
    cost = tokens["input_uncached"] * in_rate
    cost += tokens["cache_read"] * in_rate * read_mult
    if fmt == "claude":
        cost += tokens["cache_w5m"] * in_rate * ANTHROPIC_W5M
        cost += tokens["cache_w1h"] * in_rate * ANTHROPIC_W1H
    else:
        cost += tokens["cache_write"] * in_rate  # no write SKU; billed as input
    cost += tokens["output"] * out_rate
    return cost, read_mult


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--format", required=True, choices=["claude", "codex"])
    ap.add_argument("--log", required=True, help="session JSONL / rollout JSONL path")
    ap.add_argument("--model-key", required=True, help="prices.yaml model key")
    ap.add_argument("--label", required=True, help="row label (model name)")
    ap.add_argument("--tool", default=None, help="tool column (default from --format)")
    ap.add_argument("--prices", default=DEFAULT_PRICES)
    ap.add_argument("--own-tests", default="?", help="PASS/FAIL of the model's own test_duration")
    ap.add_argument("--acceptance", default="?", help="PASS/FAIL of acceptance_test.py")
    ap.add_argument("--edge-failures", default="?", help="failures+errors from acceptance run")
    ap.add_argument("--wall", default=None, help="wall-clock seconds")
    ap.add_argument("--out", default=None, help="write result JSON here")
    ap.add_argument("--allow-unpriced", action="store_true",
                    help="emit the row with cost=null when the price row is missing")
    ap.add_argument("--web-tools", choices=["void", "record", "off"], default="void",
                    help="web-tool audit mode for the session log. 'void' (default): "
                         "any web-tool invocation VOIDS the run (loud FATAL) — for "
                         "scored offline tasks. 'record': count only, never fails — for "
                         "the future web-dig task. 'off': skip the scan.")
    args = ap.parse_args()

    tool = args.tool or ("claude-code" if args.format == "claude" else "codex")
    tokens = sum_claude_jsonl(args.log) if args.format == "claude" else sum_codex_jsonl(args.log)

    # Control arm: a completed run must have real spend.
    if tokens["input_total"] + tokens["output"] <= 0:
        raise SystemExit(f"FATAL: control arm failed — zero tokens summed from {args.log}")

    # Web-tool audit (B3): a scored offline task must not reach the network.
    web_tool_count, web_tool_names = 0, []
    if args.web_tools != "off":
        web_tool_count, web_tool_names = scan_web_tools(args.log, args.format)
        if args.web_tools == "void" and web_tool_count > 0:
            raise SystemExit(
                f"FATAL: run VOIDED — {web_tool_count} web-tool invocation(s) "
                f"{web_tool_names} in {args.log}. This is an OFFLINE scored task; a "
                f"model that searched/fetched the web is not a clean measurement. "
                f"Re-run offline, or pass --web-tools record for a web-permitted task.")
        if web_tool_count > 0:
            print(f"WEB-TOOLS: recorded {web_tool_count} invocation(s) {web_tool_names} "
                  f"(record mode — not voided)", file=sys.stderr)

    prices, prices_version = load_prices(args.prices)
    row = prices.get(args.model_key)
    cost = read_mult = None
    if row:
        cost, read_mult = price(tokens, row, args.format)
    elif not args.allow_unpriced:
        raise SystemExit(
            f"FATAL: no price row for '{args.model_key}' in {args.prices} "
            f"(pricing-coverage gap — add a row or pass --allow-unpriced)")
    else:
        print(f"PRICING GAP: no row for '{args.model_key}' in {args.prices} — row emitted UNPRICED",
              file=sys.stderr)

    cache_pct = (100.0 * tokens["cache_read"] / tokens["input_total"]) if tokens["input_total"] else 0.0
    wall = f"{int(args.wall)//60}m{int(args.wall)%60:02d}s" if args.wall is not None else "?"
    reasoning = str(tokens["reasoning"]) if tokens["reasoning"] is not None else "n/a*"
    cost_s = f"${cost:.4f}" if cost is not None else "UNPRICED"

    md = (f"| {tool} | {args.label} | {args.own_tests} | {args.acceptance} | {args.edge_failures} "
          f"| {tokens['input_uncached']:,} | {tokens['cache_read']:,} | {tokens['cache_write']:,} "
          f"| {tokens['output']:,} | {reasoning} | {cache_pct:.1f}% | {cost_s} | {wall} |")

    result = {
        "tool": tool, "label": args.label, "model_key": args.model_key,
        "model_ids_seen": tokens["model_ids"], "log": os.path.abspath(args.log),
        "own_tests": args.own_tests, "acceptance": args.acceptance,
        "edge_failures": args.edge_failures,
        "tokens": tokens, "cache_read_pct": round(cache_pct, 2),
        "cost_list_usd": round(cost, 6) if cost is not None else None,
        "cache_read_mult_used": read_mult,
        "wall_seconds": int(args.wall) if args.wall is not None else None,
        "priced": row is not None,
        "web_tools_mode": args.web_tools,
        "web_tool_count": web_tool_count,
        "web_tool_names": web_tool_names,
        "prices_yaml": os.path.abspath(args.prices),
        "prices_yaml_version": prices_version,
    }
    print(md)
    print(json.dumps(result, indent=2), file=sys.stderr)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    # Cross-check: the model id recorded in the log should relate to the key
    if tokens["model_ids"] and not any(args.model_key in m or m in args.model_key
                                       for m in tokens["model_ids"]):
        print(f"WARN: log model ids {tokens['model_ids']} do not match key '{args.model_key}'",
              file=sys.stderr)


if __name__ == "__main__":
    main()
