#!/usr/bin/env python3
"""Control-arm self-test for the cronspec judge (acceptance_cronspec.py).

Proves the judge is trustworthy — i.e. that it actually tests the three original
deviations AND the four hardening rules — by running it against three targets:

  (a) control/cronspec_reference.py       (spec-faithful)      -> judge MUST PASS.
  (b) control/cronspec_vixie.py           (vixie-faithful)     -> judge MUST FAIL
      (breaks the three original deviations).
  (c) control/cronspec_naive_careful.py   (original 3 devs OK, none of the 4
      hardening rules) -> judge MUST FAIL, specifically on the hardening rules,
      while still AGREEING with the reference on the original-deviation cases.

If either (b) or (c) PASSES the judge, the judge is not exercising the difficulty
it claims to, and this script exits nonzero (loudly) so it can be fixed before
publishing.

Run: python3 control/check-cronspec-selftest.py
Exit 0 iff all three arms hold. Deterministic, offline, UTC.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_BENCH = os.path.dirname(_HERE)
_JUDGE = os.path.join(_BENCH, "acceptance_cronspec.py")
_REFERENCE = os.path.join(_HERE, "cronspec_reference.py")
_VIXIE = os.path.join(_HERE, "cronspec_vixie.py")
_NAIVE = os.path.join(_HERE, "cronspec_naive_careful.py")


def _run_judge(impl_path):
    """Copy impl_path in as cron.py in a fresh dir; return (rc, stdout+stderr)."""
    tmp = tempfile.mkdtemp(prefix="cronspec-selftest-")
    try:
        shutil.copyfile(impl_path, os.path.join(tmp, "cron.py"))
        proc = subprocess.run(
            [sys.executable, _JUDGE, tmp],
            capture_output=True, text=True,
        )
        return proc.returncode, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _result_line(out):
    m = re.search(r"^ACCEPTANCE_RESULT .*$", out, re.MULTILINE)
    return m.group(0) if m else "(no ACCEPTANCE_RESULT line)"


def _load(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _call(mod, expr, after):
    """Return ('ok', dt) or ('raise', None) or ('err', exc-type-name)."""
    try:
        return "ok", mod.next_fire(expr, after)
    except ValueError:
        return "raise", None
    except Exception as exc:  # noqa: BLE001
        return "err", type(exc).__name__


def main():
    problems = []

    # ── Arm (a): reference impl must PASS the judge ──────────────────────────
    rc_ref, out_ref = _run_judge(_REFERENCE)
    print(f"[reference]    judge rc={rc_ref}  {_result_line(out_ref)}")
    if rc_ref != 0:
        problems.append("reference impl did NOT pass the judge (should PASS):\n" + out_ref)

    # ── Arm (b): vixie impl must FAIL the judge ──────────────────────────────
    rc_vix, out_vix = _run_judge(_VIXIE)
    print(f"[vixie]        judge rc={rc_vix}  {_result_line(out_vix)}")
    if rc_vix == 0:
        problems.append(
            "vixie-faithful impl PASSED the judge — the judge does NOT test the "
            "original deviations. Fix the judge until the vixie impl fails.\n" + out_vix)

    # ── Arm (c): naive-careful impl must FAIL the judge ──────────────────────
    rc_nai, out_nai = _run_judge(_NAIVE)
    print(f"[naive-careful] judge rc={rc_nai}  {_result_line(out_nai)}")
    if rc_nai == 0:
        problems.append(
            "naive-careful impl PASSED the judge — the judge does NOT test the "
            "four hardening rules. Fix the judge until the naive impl fails.\n" + out_nai)

    ref = _load(_REFERENCE, "cronspec_reference")
    vix = _load(_VIXIE, "cronspec_vixie")
    nai = _load(_NAIVE, "cronspec_naive_careful")

    # ── Targeted proof: vixie breaks each ORIGINAL deviation ─────────────────
    print("\n-- original deviations (vixie must diverge) --")
    expr, after = "0 0 13 * fri", datetime(2026, 1, 1, 0, 0)
    r, v = ref.next_fire(expr, after), vix.next_fire(expr, after)
    print(f"[dev1] {expr!r}: reference={r}  vixie={v}")
    if not (r == datetime(2026, 2, 13) and v == datetime(2026, 1, 2) and r != v):
        problems.append(f"Deviation 1 not distinguished: ref={r} vixie={v}")

    expr, after = "5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)
    r, v = ref.next_fire(expr, after), vix.next_fire(expr, after)
    print(f"[dev2] {expr!r}: reference={r}  vixie={v}")
    if not (r.minute == 5 and v.minute == 10 and r != v):
        problems.append(f"Deviation 2 not distinguished: ref={r} vixie={v}")

    expr, after = "0 0 * * 7", datetime(2026, 1, 1, 0, 0)
    rk, _ = _call(ref, expr, after)
    vk, _ = _call(vix, expr, after)
    print(f"[dev3] {expr!r}: reference={rk}  vixie={vk}")
    if not (rk == "raise" and vk == "ok"):
        problems.append(f"Deviation 3 not distinguished: ref={rk} vixie={vk}")

    # ── Targeted proof: naive-careful breaks each HARDENING rule ─────────────
    print("\n-- hardening rules (naive-careful must diverge) --")
    # Rule 4: wrap-around range (naive rejects A>B).
    expr, after = "50-10 * * * *", datetime(2026, 1, 1, 0, 30)
    rk, rv = _call(ref, expr, after)
    nk, nv = _call(nai, expr, after)
    print(f"[rule4 wrap]     {expr!r}: reference={rv}  naive={nk}")
    if not (rk == "ok" and rv == datetime(2026, 1, 1, 0, 50) and nk == "raise"):
        problems.append(f"Rule 4 (wrap) not distinguished: ref=({rk},{rv}) naive={nk}")

    # Rule 5: step through a wrap (naive rejects the wrapped range).
    expr, after = "50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)
    rk, rv = _call(ref, expr, after)
    nk, nv = _call(nai, expr, after)
    print(f"[rule5 wrapstep] {expr!r}: reference={rv}  naive={nk}")
    if not (rk == "ok" and rv.minute == 5 and nk == "raise"):
        problems.append(f"Rule 5 (wrap-step) not distinguished: ref=({rk},{rv}) naive={nk}")

    # Rule 6: L (naive treats L as an unknown token).
    expr, after = "0 0 L 2 *", datetime(2027, 3, 1, 0, 0)
    rk, rv = _call(ref, expr, after)
    nk, nv = _call(nai, expr, after)
    print(f"[rule6 L]        {expr!r}: reference={rv}  naive={nk}")
    if not (rk == "ok" and rv == datetime(2028, 2, 29) and nk == "raise"):
        problems.append(f"Rule 6 (L) not distinguished: ref=({rk},{rv}) naive={nk}")

    # Rule 7: month-length rollover (naive clamps 31 onto the last day).
    expr, after = "0 0 31 * *", datetime(2026, 4, 1, 0, 0)
    r, n = ref.next_fire(expr, after), nai.next_fire(expr, after)
    print(f"[rule7 rollover] {expr!r}: reference={r}  naive={n}")
    if not (r == datetime(2026, 5, 31) and n == datetime(2026, 4, 30) and r != n):
        problems.append(f"Rule 7 (rollover) not distinguished: ref={r} naive={n}")

    # ── naive-careful must still AGREE with reference on original devs ────────
    print("\n-- naive-careful agrees with reference on the original deviations --")
    agree_cases = [
        ("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),   # dev1 intersection
        ("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),    # dev1 intersection
        ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),  # dev2 forward step
        ("0 0 15 * wed", datetime(2026, 1, 1, 0, 0)),   # dev1, dom<=28
        ("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),  # dow name
    ]
    for expr, after in agree_cases:
        r, n = ref.next_fire(expr, after), nai.next_fire(expr, after)
        mark = "OK" if r == n else "DIVERGE(!)"
        print(f"  {expr!r:22} ref={r} naive={n} {mark}")
        if r != n:
            problems.append(
                f"naive-careful diverges from reference on an ORIGINAL-deviation "
                f"case {expr!r}: ref={r} naive={n} (it should implement those correctly)")

    print()
    if problems:
        print("SELFTEST FAILED:")
        for p in problems:
            print("  - " + p.splitlines()[0])
            for extra in p.splitlines()[1:]:
                print("      " + extra)
        print("CRONSPEC_SELFTEST_RESULT pass=False")
        sys.exit(1)

    print("All three arms hold: reference PASSES; vixie FAILS on the original "
          "deviations; naive-careful FAILS on the four hardening rules while "
          "matching the reference on the originals.")
    print("CRONSPEC_SELFTEST_RESULT pass=True")
    sys.exit(0)


if __name__ == "__main__":
    main()
