"""Guard stage: PII filter and intent gate, evaluated before any embedding or LLM call.

Hard constraints this module enforces:

- C2 (no PII): nothing personal is accepted, echoed, or logged. User input is never
  written anywhere; if a trace line is ever wanted, the rule is: decision code + a
  SHA-256 *prefix* of the question, at most.
- C3 (no performance claims): return-, CAGR-, alpha- … shaped questions are deflected
  to the official factsheet with no numbers.
- C4 (no advice): opinion/portfolio questions are refused with a facts-only message and
  an investor-education link (AMFI/SEBI only).
- C8 (no fabrication): questions about a field the five pages never state — AUM, holdings,
  capital gains, dividends, risk statistics — are deflected to the factsheet rather than
  answered from the nearest chunk that happens to clear the score floor.

Everything here is deterministic and regex/keyword based (ADR-3) so a reviewer can read
the rules and predict the outcome. Order is fixed: PII first, then intent — a question
that contains both a PAN and "should I buy" must be BLOCKED, not refused.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

import config

GuardAction = Literal["block", "refuse", "deflect", "out_of_scope", "allow"]

# ---------------------------------------------------------------------------
# Message constants
# ---------------------------------------------------------------------------

PII_MESSAGE = (
    "I can't accept personal details here. Please keep PAN, Aadhaar, account or folio "
    "numbers, OTPs, email addresses, and phone numbers out of your question — and never "
    "paste them anywhere. Ask me a facts-only question about any of the five HDFC "
    "schemes I cover instead."
)

ADVICE_REFUSAL = (
    "This assistant is facts-only and does not give investment advice. I can answer "
    "specific factual questions like expense ratio, exit load, minimum investment, "
    "lock-in, riskometer, or benchmark. You can learn more at the SEBI investor "
    f"corner: {config.EDUCATION_URLS['sebi_mutual_funds']}"
)

PERFORMANCE_DEFLECTION = (
    "I don't state, compute, or compare performance or returns. The official scheme "
    f"factsheets are published by the AMC here: {config.FACTSHEET_URL}"
)

NOT_COVERED_DEFLECTION = (
    "That figure isn't in the source pages I cover, so I can't answer it without "
    "making it up. The official scheme factsheet has it: "
    f"{config.FACTSHEET_URL}"
)

OUT_OF_SCOPE_MESSAGE = (
    "I only cover these five HDFC funds: "
    + ", ".join(list(config.SUPPORTED_SCHEMES.values()))
    + ". If your question is about one of those, rephrase it as a factual question."
)

# ---------------------------------------------------------------------------
# PII patterns (PRD 12.1 / ARCHITECTURE 5.2)
# ---------------------------------------------------------------------------

PII_PATTERNS: tuple[tuple[str, str], ...] = (
    ("pan", r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    ("aadhaar", r"\b[0-9]{4}\s?[0-9]{4}\s?[0-9]{4}\b"),
    ("account_folio", r"\b[0-9]{8,18}\b"),
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("phone", r"(?:\+91[\s-]?)?\b[6-9][0-9]{9}\b"),
)
_OTP_CONTEXT_RE = re.compile(r"\b(otp|one\s*time\s*(password|pin)?|verification\s*code)\b", re.I)
_OTP_DIGITS_RE = re.compile(r"\b[0-9]{4,6}\b")


@dataclass
class PIIMatch:
    hit: bool
    patterns: list[str]  # names of matched patterns, never the matched values


def pii_scan(text: str) -> PIIMatch:
    matched: list[str] = []
    for name, pattern in PII_PATTERNS:
        if re.search(pattern, text):
            matched.append(name)
    if _OTP_CONTEXT_RE.search(text) and _OTP_DIGITS_RE.search(text):
        matched.append("otp")
    return PIIMatch(hit=bool(matched), patterns=matched)


# ---------------------------------------------------------------------------
# Intent gate (ADR-3: keywords and regex, deterministic, reviewer-readable)
# ---------------------------------------------------------------------------

_ADVICE_PATTERNS: tuple[str, ...] = (
    r"\bshould\s+i\b",
    r"\bshould\s+we\b",
    r"\bbuy\b",
    r"\bsell\b",
    r"\binvest in\b",
    r"\ballocate\b",
    r"\bswitch\b",
    r"\bredeem\b",
    r"\bis it good\b",
    r"\bworth it\b",
    r"\bsuggest",
    r"\brecommend",
    r"\bwhich is better\b",
    r"\bbest for me\b",
    r"\bmy portfolio\b",
    r"\bmy holdings\b",
    r"\bsuitable for me\b",
)

_PERFORMANCE_PATTERNS: tuple[str, ...] = (
    r"\breturn\b",
    r"\breturns\b",
    r"\bCAGR\b",
    r"\b[0-9]+[- ]year return\b",
    r"\bbest performing\b",
    r"\btop performing\b",
    r"\balpha\b",
    r"\branking",
    r"\branked\b",
    r"\bvs\.? benchmark\b",
    r"\boutperform",
    r"\bsince inception\b",
    r"\bNAV\b",
    r"\bsip returns\b",
    r"\bprofit\b",
)

_UNCOVERED_PATTERNS: tuple[str, ...] = (
    r"\bAUM\b",
    r"\bassets under management\b",
    r"\bnet assets?\b",
    r"\bfund size\b",
    r"\bcorpus\b",
    r"\bholdings\b",
    r"\btop\s*10\b",
    r"\bcapital gains?\b",
    r"\bdividends?\b",
    r"\bvolatility\b",
    r"\bsharpe\b",
    r"\bsortino\b",
    r"\bseat loss\b",
    r"\bcustodian\b",
    r"\btrustee\b",
)
"""Fields the five source pages do not state, verified against the indexed corpus.

These are not performance claims and they are not other funds' schemes, so neither
`_PERFORMANCE_PATTERNS` nor the out-of-corpus rules covered them. The questions fell
through to retrieval, which cleared the score floor on unrelated text -- "What is the
AUM?" returned `confident=True` citing a `Charges and fees` chunk, and a memory-scoped
follow-up returned the right fund's wrong field. Answering a field the sources never
mentioned is a fabrication risk, so the honest move is to deflect to the factsheet.

Every pattern here was checked against the built collection: 25 chunks, zero occurrences
of AUM, net assets, holdings, capital gains, dividends, or any risk statistic. The list is
equally a list of what NOT to match, because several near neighbours ARE in the corpus and
deflecting them would refuse a question the sources can answer:

    "capital appreciation"   present (5)  -> so the pattern is `capital gains?`, not `capital`
    "portfolio turnover"     present (5)  -> so `portfolio` is not deflected
    "category"               present (5)
    "registrar"              present (5)  -> but custodian and trustee are absent, so those two
                                         are listed and `registrar` is not
    "AMC"                    present (5)

`\\bcorpus\\b` is here for the mutual-fund sense (a fund's corpus) and not for "the corpus
of statements I read", which is a phrasing this app never receives.
"""

# Fund houses we do NOT cover, used to route "scheme not in the 5" questions. HDFC is
# deliberately absent: every supported scheme is an HDFC fund.
_OTHER_HOUSES_RE = re.compile(
    r"\b(parag|sbi|axis|icici|kotak|mirae|nippon|aditya birla|birla|uti|motilal|"
    r"quant|dsp|edelweiss|bandhan|franklin templeton|franklin|tata|hdfc securities)\b",
    re.I,
)

# Scheme/asset types that are never one of the 5 supported schemes.
_OTHER_ASSET_RE = re.compile(
    r"\b(index fund|index etf|gold fund|mid ?cap fund|multi ?cap fund|arbitrage fund|"
    r"liquid fund|debt fund|gilt fund|ultra short term|international fund)\b",
    re.I,
)

_ADVICE_RE = re.compile("|".join(_ADVICE_PATTERNS), re.I)
_PERFORMANCE_RE = re.compile("|".join(_PERFORMANCE_PATTERNS), re.I)
_UNCOVERED_RE = re.compile("|".join(_UNCOVERED_PATTERNS), re.I)

Intent = Literal["ALLOWED", "ADVICE", "PERFORMANCE", "OUT_OF_CORPUS", "UNCOVERED"]


def classify_intent(question: str) -> Intent:
    if _ADVICE_RE.search(question):
        return "ADVICE"
    if _PERFORMANCE_RE.search(question):
        return "PERFORMANCE"
    lowered = question.lower()
    if _OTHER_HOUSES_RE.search(lowered) or _OTHER_ASSET_RE.search(lowered):
        return "OUT_OF_CORPUS"
    if _UNCOVERED_RE.search(question):
        # Last, so "what is the AUM of the SBI Magnum fund?" is still routed as the
        # wrong-house question it is, rather than as a missing field.
        return "UNCOVERED"
    return "ALLOWED"


# ---------------------------------------------------------------------------
# Decision and entry point
# ---------------------------------------------------------------------------


@dataclass
class GuardDecision:
    action: GuardAction
    message: str | None = None


def run_guards(question: str) -> GuardDecision:
    """PII first, then intent (ARCHITECTURE 5.2). Evaluated before embedding."""
    if pii_scan(question).hit:
        return GuardDecision("block", PII_MESSAGE)  # C2
    intent = classify_intent(question)
    if intent == "ADVICE":
        return GuardDecision("refuse", ADVICE_REFUSAL)  # C4
    if intent == "PERFORMANCE":
        return GuardDecision("deflect", PERFORMANCE_DEFLECTION)  # C3
    if intent == "OUT_OF_CORPUS":
        return GuardDecision("out_of_scope", OUT_OF_SCOPE_MESSAGE)
    if intent == "UNCOVERED":
        return GuardDecision("deflect", NOT_COVERED_DEFLECTION)
    return GuardDecision("allow", None)


def safe_prefix(question: str, kept: int = 12) -> str:
    """The only logging form ever allowed for user input: a SHA-256 prefix."""
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:kept]


if __name__ == "__main__":
    for probe in [
        "Should I buy the ELSS fund?",
        "My PAN is ABCDE1234F",
        "Which fund has the best 5-year return?",
        "Expense ratio of Parag Parikh Flexi Cap?",
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        "What is the exit load?",
    ]:
        d = run_guards(probe)
        print(f"{probe!r:65} -> {d.action}")
        if d.message:
            print(f"    message: {d.message}")