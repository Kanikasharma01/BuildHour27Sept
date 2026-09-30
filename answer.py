"""Stage 2 answering: grounded generation, deterministic post-validation, fallback.

Pipeline per question: retrieve -> build context -> call Groq (temperature 0) ->
validate deterministically -> repair ONCE -> extractive fallback. Guards are NOT
called here: `app.py` runs them first and returns early, keeping ingest / guards /
answer concerns separate.

The `Last updated from sources:` timestamp is appended by code after validation; the
LLM never writes it (C7). Post-validation is code, not another LLM call (ADR-6).
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import config
import guards
from retrieve import Hit, build_context, resolve_question, retrieve
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Contract machinery
# ---------------------------------------------------------------------------

_SENT = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'(\[])|\n{2,}')
_URL_RE = re.compile(r"https?://\S+")

# Performance-claim language, kept sharper than the intent-gate list so grounded
# facts like "Benchmark: NIFTY 500 Total Return Index" are NOT flagged (that is an
# index name, not a claim). A claim is an explicit performance verb/phrase, or the
# word "return(s)" within a few characters of a % figure.
_PERF_CLAIM_RE = re.compile(
    r"\b(cagr|alpha|outperform\w*|best performing|top performing\w*|"
    r"annualiz\w+|sip return\w*|inception\w* return\w*)\b"
    r"|"
    r"\b\d+(?:\.\d+)?%?\s+(?:year\w+ )?return\w*\b"
    r"|"
    r"\breturn\w*\b[^\n]{0,40}%"
    r"|"
    r"\b%\b[^\n]{0,40}\breturn\w*\b",
    re.I,
)

_ADVICE_CLAIM_RE = re.compile(
    r"\b(you should|we recommend|recommended for you|you must|good investment|"
    r"safe investment|i suggest you)\b",
    re.I,
)

DEGRADED_NOTICE = (
    "Answer generation is unavailable right now — here is the matching text from "
    "the source page, with the link."
)

NOT_IN_SOURCES_MESSAGE = (
    "I couldn't find an answer to that in the source pages I cover. I can answer "
    "factual questions about these five HDFC funds: "
    + ", ".join(list(config.SUPPORTED_SCHEMES.values()))
    + "."
)

AnswerMode = Literal["generated", "extractive", "not_in_sources", "refused"]


@dataclass
class Answer:
    text: str
    source_url: str | None
    source_title: str | None
    last_updated: str
    hits: list[Hit]
    mode: AnswerMode
    notice: str | None = None


def count_sentences(text: str) -> int:
    parts = [p for p in _SENT.split(text) if p.strip()]
    return len(parts) if parts else (1 if text.strip() else 0)


def _urls(text: str) -> list[str]:
    return _URL_RE.findall(text)


def validate_answer(text: str, hits: list[Hit]) -> tuple[bool, list[str]]:
    """Deterministic checks in order: length, citation, allow-list + presence,
    no performance/advice language, no PII echo. Returns (ok, violations)."""
    violations: list[str] = []

    n_sent = count_sentences(text)
    if n_sent > 3:
        violations.append(f"answer has {n_sent} sentences (max 3)")

    urls = _urls(text)
    if len(urls) != 1:
        violations.append(f"answer must contain exactly one URL, found {len(urls)}")

    if urls:
        url = urls[0]
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        allowed = any(
            host == d or host.endswith(f".{d}") for d in config.ALLOWED_DOMAINS
        )
        if not allowed:
            violations.append(f"URL domain {host!r} is not in the source allow-list")
        hit_urls = {h.metadata["source_url"] for h in hits}
        if url not in hit_urls:
            violations.append("URL was not among the retrieved source chunks")

    if _PERF_CLAIM_RE.search(text):
        violations.append("answer contains a performance/return claim")
    if _ADVICE_CLAIM_RE.search(text):
        violations.append("answer contains advice language")

    if guards.pii_scan(text).hit:
        violations.append("answer echoes personal information")

    return not violations, violations


def extractive_fallback(hits: list[Hit]) -> Answer:
    """Highest-ranked chunk's first two sentences, labelled as source text (A5)."""
    top = hits[0]
    sentences = count_sentences(top.document)
    excerpt = top.document
    for _ in range(sentences - 2):
        if _SENT.search(excerpt):
            excerpt = _SENT.split(excerpt, 1)[0]
    fetched = top.metadata["fetched_at"]
    body = f"From the source page:\n{excerpt}"
    text = (
        f"{body}\n\nSource: {top.metadata['source_url']}\n"
        f"Last updated from sources: {fetched}"
    )
    return Answer(
        text=text,
        source_url=top.metadata["source_url"],
        source_title=top.metadata["page_title"],
        last_updated=fetched,
        hits=[top],
        mode="extractive",
    )


def call_groq(question: str, context: str, violations: list[str] | None = None) -> str | None:
    """Call Groq at temperature 0. Every failure -> None (never propagate, never
    fabricate); the caller turns None into the extractive fallback.

    `violations` names the previous answer's failures so the repair attempt is a
    fresh completion with explicit instructions (ADR-6), not a second LLM judge.

    Reasoning is pinned to `config.GROQ_REASONING_EFFORT` because the pinned model is a
    reasoning model: left unbounded it spends most of `max_tokens` thinking and returns an
    empty `content`, which is indistinguishable from a provider outage unless `_usable`
    treats blank as a failure. See that function and the config comment for the numbers.
    """
    system = config.SYSTEM_PROMPT
    if violations:
        system += (
            "\n\nYour previous answer was rejected for these violations:\n- "
            + "\n- ".join(violations)
            + "\nRewrite it to fix every violation, keeping 1-3 sentences and the "
            "exactly-one 'Source: <url>' line."
        )
    try:
        from groq import Groq

        client = Groq(api_key=config.require_api_key())
        request = {
            "model": config.groq_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"{context}\n\nQUESTION: {question}"},
            ],
            "temperature": 0,
            "max_tokens": 250,
        }
        try:
            completion = client.chat.completions.create(
                **request,
                extra_body={"reasoning_effort": config.GROQ_REASONING_EFFORT},
            )
        except Exception as exc:
            # A model that does not accept `reasoning_effort` must not break answering, so
            # retry once without it rather than degrading every turn to source text. This is
            # reported, because silently swallowing it here is how a malformed request looks
            # exactly like a provider outage from the outside.
            _warn_failure("reasoning_effort rejected", exc)
            completion = client.chat.completions.create(**request)
        return (completion.choices[0].message.content or "").strip()
    except Exception as exc:
        _warn_failure("call failed", exc)
        return None


def _compose_generated(body: str, newest: str) -> tuple[str, str | None]:
    """Take the LLM body (with its Source: line), return (display_text, source_url).

    The citation and the timestamp are both code-appended here so the model writes
    neither authoritatively (C6/C7).
    """
    urls = _urls(body)
    source_url = urls[0] if urls else None
    body = re.sub(r"^\s*Source:\s*\S+\s*$", "", body, flags=re.M).strip()
    if source_url:
        text = f"{body}\n\nSource: {source_url}\nLast updated from sources: {newest}"
    else:
        text = body
    return text, source_url


def _warn_failure(context: str, exc: BaseException) -> None:
    """Surface a swallowed generation failure without ever echoing the question.

    `call_groq` turns every failure into `None` on purpose, so the orchestrator can degrade
    instead of crashing. The cost of that is total silence: a malformed request, a missing
    key and a real outage all reached the user as the same "unavailable" notice, and a
    request-assembly mistake went unnoticed behind it. Only the exception *type* and a
    fixed label are reported, which is consistent with C2 -- nothing derived from the
    question, its context, or its answer is included.
    """
    warnings.warn(f"answer.call_groq: {context} ({type(exc).__name__})", RuntimeWarning,
                  stacklevel=3)


def _usable(text: str | None) -> bool:
    """True if the model actually said something.

    A blank reply is a failure, not an answer, and it is not the same failure as `None`.
    `gpt-oss-120b` is a reasoning model: it bills part of `max_tokens` against
    `reasoning_tokens` (measured 156-168 of a ~218-token completion here), so when the
    reasoning alone overruns the budget Groq returns `finish_reason="length"` with an
    empty `content` and `call_groq` hands back `""`.

    Checking only `is None` let that empty string through as if it were a real answer. It
    then failed validation for the wrong reason ("must contain exactly one URL, found 0"),
    consumed the single repair attempt, and surfaced as an extractive fallback whose
    `DEGRADED_NOTICE` claimed the *provider* was unavailable. Multi-fund comparisons hit
    this most often, because synthesising across funds takes the most reasoning.
    """
    return bool(text and text.strip())


def answer_question(question: str, history: Sequence[str] = ()) -> Answer:
    """Orchestrator. Does NOT call guards (app.py owns that early-exit path).

    `history` is the bounded window of prior *allowed* questions (FR-18). The caller is
    responsible for the bound and for only ever passing allowed turns; here it is
    re-trimmed so no caller can exceed `MEMORY_TURNS`. It is used to resolve what an
    elliptical follow-up refers to, never to widen the question.
    """
    window = list(history)[-config.MEMORY_TURNS :]
    result = retrieve(question, history=window)
    if not result.confident:
        return Answer(
            text=(
                NOT_IN_SOURCES_MESSAGE
                + "\n\nThe topic wasn't found in my source pages, so I can't cite it."
            ),
            source_url=None,
            source_title=None,
            last_updated="",
            hits=[],
            mode="not_in_sources",
        )

    # AQ-2: rewrite only the question the model sees, naming the fund the follow-up
    # referred to. The transcript still stores exactly what the user typed.
    prompt_question = resolve_question(question, window)[0]
    context, newest = build_context(result)
    raw = call_groq(prompt_question, context)
    if not _usable(raw):
        fallback = extractive_fallback(result.hits)
        fallback.notice = DEGRADED_NOTICE
        return fallback

    ok, violations = validate_answer(raw, result.hits)
    if not ok:
        raw2 = call_groq(prompt_question, context, violations=violations)
        if not _usable(raw2):
            return extractive_fallback(result.hits)
        ok2, _ = validate_answer(raw2, result.hits)
        if not ok2:
            return extractive_fallback(result.hits)
        raw = raw2

    text, source_url = _compose_generated(raw, newest)
    return Answer(
        text=text,
        source_url=source_url,
        source_title=result.hits[0].metadata["page_title"],
        last_updated=newest,
        hits=result.hits,
        mode="generated",
    )


def _probe() -> int:
    probes = [
        "What is the lock-in period for the ELSS fund?",
        "What is the exit load?",
        "Who is the fund manager of the large cap fund?",
    ]
    for question in probes:
        answer = answer_question(question)
        print(f"Q: {question}")
        print(f"mode: {answer.mode}")
        if answer.notice:
            print(f"notice: {answer.notice}")
        print(answer.text)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_probe())