"""Manual REPL for testing the grounded-answer pipeline before the UI exists.

    python -m ask

Type a question, get the answer plus the retrieval evidence behind it. This is the
pre-UI stand-in for `app.py`: it runs `guards` first and returns early on
block/refuse/deflect/out_of_scope, then hands anything allowed to `answer.answer_question`.

Prints `mode` on every turn because it is the single most informative field while tuning:
`generated` (LLM answer passed the contract), `extractive` (the LLM failed or the key is
missing — source text is shown instead), `not_in_sources` (retrieval was below the score
floor, so the LLM was never called), `refused` (a guard stopped it).

The top-hit line is printed alongside the answer so a wrong answer can be blamed on the
right stage: a low similarity or a sibling fund's page means retrieval, and no amount of
prompt work will fix it.

Like `app.py`, this keeps a `MEMORY_TURNS` window of allowed questions so follow-ups
("its exit load?") resolve to the fund the session was already about (FR-18). `:hits`
prints the scheme a question was scoped to, which is the fastest way to see whether a
wrong answer came from referent resolution or from ranking.
"""

from __future__ import annotations

import sys

import config
import guards
from answer import answer_question

BANNER = (
    "ask - type a question and press enter. "
    "commands: :hits show the retrieved chunks, :quit exit."
)


def _show_hits(question: str, history: list[str]) -> None:
    from retrieve import retrieve, resolve_question

    resolved, scheme = resolve_question(question, history)
    result = retrieve(question, history=history)
    print(f"confident={result.confident}  hits={len(result.hits)}  "
          f"floor={config.SCORE_FLOOR}  scheme={scheme or '(unscoped)'}")
    if scheme and resolved != question:
        print(f"  resolved: {resolved}")
    for i, hit in enumerate(result.hits, 1):
        print(
            f"  [{i}] {hit.similarity:.3f}  {hit.metadata['scheme_category']:<18}"
            f" {hit.metadata['heading_path']}"
        )
        print(f"      {hit.metadata['source_url']}")


def _respond(question: str, history: list[str]) -> None:
    decision = guards.run_guards(question)
    if decision.action != "allow":
        print(f"[{decision.action}] {decision.message}")
        return

    answer = answer_question(question, history=history)
    print(f"[{answer.mode}]")
    if answer.notice:
        print(f"notice: {answer.notice}")
    print(answer.text)
    if answer.hits:
        top = answer.hits[0]
        print(f"  ^ top hit: {top.similarity:.3f}  "
              f"{top.metadata['page_title']} | {top.metadata['heading_path']}")
    print()
    # Appended only after the guards pass, mirroring app.py: rejected turns are never
    # remembered, and the window is trimmed so it cannot outgrow MEMORY_TURNS.
    history.append(question)
    del history[:-config.MEMORY_TURNS]


def main() -> int:
    # The model emits typographic characters (em dash, non-breaking hyphen U+2011) that a
    # default Windows cp1252 console cannot encode; without this the REPL dies mid-answer
    # on an encoding error rather than on anything to do with the pipeline.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

    print(BANNER)
    if not config.api_key_present():
        print(
            "warning: GROQ_API_KEY is not set - every answer will come back "
            "'extractive' (raw source text). Set it in .env to test generation.\n"
        )

    history: list[str] = []
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in (":quit", ":q", "exit"):
            return 0
        if line == ":hits":
            line = input("hits for> ").strip()
            if line and line not in (":quit", ":q"):
                _show_hits(line, history)
            continue
        _respond(line, history)


if __name__ == "__main__":
    raise SystemExit(main())
