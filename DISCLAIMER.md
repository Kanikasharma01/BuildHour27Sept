# Disclaimer

**Facts-only. No investment advice.**

This assistant answers factual questions about selected HDFC AMC mutual fund schemes
using only the public source pages listed in `SOURCES.md`. It does not recommend, rate,
or compare schemes, and it does not compute or report returns. Fund facts such as fees,
exit loads, and minimum investments change over time — always verify on the official
factsheet and consult your financial adviser before investing. Do not share PAN,
Aadhaar, account numbers, OTPs, or any personal information here.

## Where this appears in the UI

`app.py` reads this file and renders the text above, unchanged, in two places so it cannot
be dismissed or scrolled past:

1. **Below the title**, in a collapsed "Full disclaimer" panel, on every rerun.
2. **First screen only**, as the panel's default open state before any question is asked.

The bold heading, "Facts-only. No investment advice.", is rendered separately and
immediately under the title on every rerun, so the persistent FR-20 line is visible
without expanding anything.

The wording is the PRD Appendix B snippet verbatim (PRD.md §20 B). `app.py` does not
paraphrase this text and does not add marketing language; if the snippet changes, change
it here and the UI follows. The one-line summary above the panel in `app.py` is an
abbreviation of this text, not a substitute for it — the full text is always one click
away.
