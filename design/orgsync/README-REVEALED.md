# orgsync design artifacts — revealed 2026-07-23

`orgsync` graduated to the Anchor Set on 2026-07-23 (SATURATED: six of seven
roster models passed every one of their runs). The prompt, the judge and these
design artifacts are public from that date.

## Why every file here still says "SEALED"

Because **we are not allowed to edit them**, and that is the point.

Each of these was hash-committed in `TASKS.md` *before the task was ever run*:

| artifact | sha256 |
|---|---|
| `../../prompts/orgsync.md` | `94950b00fd82cc86…` |
| `../../acceptance_orgsync.py` | `b948f904bfd72ffc…` |
| `FIXTURES.md` | `c03595a92ec80b36…` |
| `JUDGE_SPEC.md` | `a03c23f610cb1875…` |
| `CONTROLS.md` | `d4206322e35c11b8…` |

All five were re-verified against those published values at graduation and all
five matched. Tidying a stale "SEALED" banner would change the bytes, change the
hash, and destroy the only evidence that the task we ran is the task we
committed to before we knew who would fail it.

So the banners stay, wrong and useful. They were true when written, and the fact
that nobody has touched them since is exactly what the hash proves. Anchor tasks
are never edited — not the prompt, not the judge, not a typo (`PROTOCOL.md`
§5.1). This file is the correction, appended rather than applied.
