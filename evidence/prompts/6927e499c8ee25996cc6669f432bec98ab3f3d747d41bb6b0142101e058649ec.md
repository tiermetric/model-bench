# Cross-model / cross-tool TIER demo — canonical task prompt

**Purpose:** the SAME task run on Codex and on four Claude Code models (Fable, Opus, Sonnet, Haiku), each in a fresh repo, captured by TIER, to compare token-spend-per-passing-outcome. This is a demonstration of TIER's measurement, not a rigorous benchmark. Paste verbatim into every run.

---

You are working in a fresh, empty Python project. Implement a duration utility and a standard-library test suite for it. Use only the Python standard library — no third-party packages.

Create duration.py with exactly two public functions:

1. parse_duration(text: str) -> int
   Parse a human-readable duration string into total whole seconds.
   - Units: d = 86400s, h = 3600s, m = 60s, s = 1s. Unit letters are lowercase only.
   - Input is one or more "<non-negative-integer><unit>" segments concatenated with NO spaces, e.g. "1h30m", "2d4h", "90s", "1d2h3m4s".
   - Numbers are non-negative integers with NO upper bound per component — "1h90m" is VALID and equals 9000. Leading zeros are allowed: "01h" == "1h".
   - Units must appear in strictly descending order of size (d, then h, then m, then s), and each unit may appear at most once. Only the unit ORDER and uniqueness are enforced — component magnitudes are unbounded.
   - Leading/trailing whitespace on the whole string is ignored; any whitespace INSIDE the string is invalid.
   - Raise ValueError for: empty or whitespace-only input; an unknown or uppercase unit; a bare number with no unit, or a unit with no number; a non-integer or negative number; whitespace inside; units out of order; a duplicate unit.

2. format_duration(seconds: int) -> str
   The inverse: the canonical SHORTEST string — largest units first, components with a zero value omitted.
   - Input is an int number of seconds. Raise ValueError for negative input.
   - format_duration(0) returns "0s" (the only case that emits a zero component).
   - Always use the largest fitting units: format_duration(86400) == "1d" (never "24h").
   - It MUST satisfy: parse_duration(format_duration(n)) == n for every integer n >= 0. (The reverse is NOT required — parse also accepts non-canonical inputs like "1h90m", which format renders as "2h30m".)

Worked examples that must hold:
   parse_duration("1h30m")    == 5400
   parse_duration("2d")       == 172800
   parse_duration("1d2h3m4s") == 93784
   parse_duration("90s")      == 90
   parse_duration("1h90m")    == 9000
   parse_duration("01h")      == 3600
   parse_duration("")           raises ValueError
   parse_duration("1x")         raises ValueError
   parse_duration("1H")         raises ValueError
   parse_duration("30m1h")      raises ValueError
   parse_duration("1h1h")       raises ValueError
   parse_duration("5")          raises ValueError
   format_duration(5400)      == "1h30m"
   format_duration(93784)     == "1d2h3m4s"
   format_duration(86400)     == "1d"
   format_duration(0)         == "0s"
   format_duration(-1)          raises ValueError

Then create test_duration.py using Python's standard-library unittest (no pytest) that covers every rule, every worked example, each error case, and the round-trip property across a range of values.

Do not create any other files. When finished, `python3 -m unittest test_duration` must pass with zero failures.
