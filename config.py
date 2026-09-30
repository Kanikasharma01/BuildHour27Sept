"""Single source of truth for paths, model ids, thresholds, and the source allow-list.

Every other module reads its tunables from here. No magic numbers in logic.
Secrets come from the environment only and are never hardcoded, logged, or printed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

try:
    from dotenv import load_dotenv

    load_dotenv()
    DOTENV_AVAILABLE = True
except ImportError:  # Phase 0 installs python-dotenv; tolerate its absence so
    DOTENV_AVAILABLE = False  # `import config` still works before the install.


SYSTEM_PROMPT = """You answer factual questions about five HDFC mutual fund schemes using ONLY the [SOURCE n] blocks provided below. Rules, in order:
1. Answer ONLY from the [SOURCE n] blocks. If the answer is not there, say you could not find it in the source pages. Never use prior knowledge.
2. Write 1 to 3 sentences. No preamble, no bullet lists, no sign-off, no offer of further help.
3. End with exactly one line \"Source: <url>\" using a URL that appears in a SOURCE block.
4. Never state, compute, or compare a return or performance figure. Never say \"you should\", \"we recommend\", or any suitability judgement.
5. Never repeat a personal identifier, even if one appears in the context.
6. Facts only. If asked for an opinion, say you do not give investment advice."""


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384

MAX_EMBED_TOKENS = 256
"""The embedder's max_seq_length.

Anything longer is silently truncated by the tokenizer: the tail never reaches the
vector, so facts in it become unretrievable and no error is raised anywhere. Chunking
must therefore assert every chunk fits under this limit.
"""


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

CHROMA_PATH = "./chroma_db"
COLLECTION = "mf_faqs"
COLLECTION_METADATA = {"hnsw:space": "cosine"}
"""Cosine space so a retrieval distance becomes a similarity via 1 - distance."""

RAW_DIR = "./data/raw"
CHUNKS_TXT = "./data/chunks.txt"


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT = 30
FETCH_RETRIES = 3
FETCH_BACKOFF = 1.5
RAW_SUFFIX = ".txt"

MIN_WORDS_PER_SOURCE = 60
"""Minimum usable words per source page.

Deliberately far below the 300-word rule of thumb, because these sources are
JSON fact records rather than prose: a correctly extracted scheme yields roughly
200 words of `Field: Value` lines. A low floor would not catch a real failure, so
ingest also asserts coverage of the core facts, which is the meaningful test.
"""

CORE_FACT_KEYS = (
    "expense_ratio",
    "exit_load",
    "min_sip_investment",
    "lock_in",
    "nfo_risk",
    "benchmark",
)


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

CHUNK_TARGET_WORDS = 200
CHUNK_MAX_WORDS = 220
CHUNK_OVERLAP_PCT = 0.20
CHUNK_MIN_WORDS = 10
"""Chunks are one section each (see CHUNKING.md). The lowest section observed in the
corpus is the 14-word scheme objective, which is legitimate, citable prose. The floor
exists only to catch accidental fragments, not to drop short-but-complete sections;
CHUNK_OVERLAP_PCT applies only if a section ever exceeds CHUNK_MAX_WORDS and has to
spill, which no section in this corpus does."""


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------

TOP_K = 5
SCORE_FLOOR = 0.25
MMR_LAMBDA = 0.7

MEMORY_TURNS = 10
"""How many prior allowed user turns a follow-up may look back at (FR-18).

Deliberately a *window*, not a growing transcript: referent resolution only needs the
most recent turn that named a scheme, and an unbounded log would make behaviour depend on
session length. Only turns that passed the guards are ever offered as memory, so this
window can never reintroduce blocked PII."""


GROQ_REASONING_EFFORT = "low"
"""Reasoning budget for the pinned Groq model, which is a reasoning model.

Measured on a multi-fund comparison, 8 calls per setting, `max_tokens=250`:

    no effort, 250 tokens   4/8 replies came back EMPTY  (reasoning mean 202, max 248)
    no effort, 400 tokens   4/8 EMPTY                    (reasoning mean 300, max 398)
    no effort, 600 tokens   1/8 EMPTY                    (reasoning mean 374, max 593)
    effort=medium, 250      7/8 EMPTY                    (reasoning mean 237, max 248)
    effort=low,    250      0/8 EMPTY                    (reasoning mean  36, max  36)

Unbounded reasoning is the failure: the model bills part of `max_tokens` against
`reasoning_tokens` and will spend nearly all of it, and what it leaves for the answer
varies run to run, so a bigger budget only shrinks the failure rather than removing it --
the same configuration measured 0/8 and then 1/8 on repeat runs. `low` pins reasoning at a
constant 36 tokens, so the answer always fits the existing 250-token budget.

It is also cheaper, not dearer: 36 reasoning tokens against the 162-593 the same calls
used before, and a comparison that previously degraded to a raw source-text fallback now
returns a normal answer. The cost is a less thoroughly reasoned answer, which for
one-to-three sentences copied from a single source page is not a meaningful loss."""

QUERY_ALIASES: tuple[tuple[str, str], ...] = (
    (r"expense\s*ratio", "Expense ratio charges and fees"),
    (r"exit\s*load", "Exit load charges and fees"),
    (r"\bsip\b", "Minimum SIP investment Investment limits"),
    (r"lock[- ]?in", "Lock-in period Investment limits"),
    (
        r"min(imum)?\s*(?:lump sum|investment|invest|amount)",
        "Minimum lump sum investment Investment limits",
    ),
    (r"risk(meter)?|how risky", "Riskometer Risk and benchmark"),
    (r"benchmark", "Benchmark index Risk and benchmark"),
    (
        r"\b(?:fund\s+)?manag(?:e[sd]?|ers?|ing)\b|\bin charge of\b|\bwho runs\b",
        "Fund manager Scheme identity",
    ),
)
"""Query-expansion aliases: (regex on the question, vocabulary to append).

Motivation, measured in Phase 5: terse, two-token topic queries ("Exit load?") score
~0.12-0.22 against a 50-token chunk even when the chunk literally contains the phrase,
because a short query vector is diluted by the chunk's other field tokens. That puts a
*legitimate* core-fact question below SCORE_FLOOR while an embedding of "asdfghjkl
nonsense query" sits at ~0.16 — no single floor separates them.

The expansion appends the corpus's own canonical labels and section headings when the
question mentions that topic, e.g. "Exit load?" -> "Exit load? Exit load charges and
fees" (0.35 vs 0.12). It is deterministic and reviewable (ADR-3). Junk queries match no
alias, so they are untouched. The expanded text is used for the query embedding ONLY;
the user's original question is what the LLM is asked.

The fund-manager alias covers verb forms ("manages", "managed by", "in charge of"), not
just the literal phrase "fund manager": with the phrase-only pattern, "who manages hdfc
large cap" matched no alias, the `Scheme identity` chunk fell out of the top-5 entirely,
and the assistant answered "could not find" for a fact sitting in its own corpus. The
pattern deliberately does NOT match "management": "fund management" is how users ask
about fund size, which is a different (and out-of-scope) fact, so aliasing it to the
manager chunk would answer a different question than the one asked."""


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------

_ENV_FILE = ".env"
_ENV_TEMPLATE = ".env.example"


def api_key_present() -> bool:
    return bool(os.environ.get("GROQ_API_KEY", "").strip())


def require_api_key() -> str:
    """Return the Groq API key, or raise an actionable error.

    Not called at import time: the app must still start in the no-key degraded mode
    described in ARCHITECTURE.md 5.6, where retrieval works and the answer falls back
    to extracted source text.
    """
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            f"GROQ_API_KEY is not set. Copy {_ENV_TEMPLATE} to {_ENV_FILE}, set "
            f"GROQ_API_KEY and GROQ_MODEL there, and restart. GROQ_MODEL must be "
            f"an explicit model id from the current Groq model list. Never commit "
            f"{_ENV_FILE}."
        )
    return key


def groq_model() -> str:
    model = os.environ.get("GROQ_MODEL", "").strip()
    if not model:
        raise RuntimeError(
            f"GROQ_MODEL is not set. Set it in .env to an explicit model id from the "
            f"current Groq model list. Do not rely on a provider alias: aliases change "
            f"what they point at, which would make the demo non-reproducible."
        )
    return model


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceSpec:
    """One public page to ingest.

    `scheme_slug` must be unique per source page, not merely per scheme: chunk_index
    restarts at 0 for each page, so the Chroma id f"{scheme_slug}::{chunk_index:03d}"
    is only unique if the slug identifies the page.
    """

    scheme_name: str
    scheme_category: str
    scheme_slug: str
    url: str
    source_type: str = "scheme_page"


SOURCE_URLS: list[SourceSpec] = [
    SourceSpec(
        scheme_name="HDFC Large Cap Fund - Direct Growth",
        scheme_category="large_cap",
        scheme_slug="hdfc_large_cap_direct_growth",
        url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
    ),
    SourceSpec(
        scheme_name="HDFC Equity (Flexi Cap) Fund - Direct Growth",
        scheme_category="flexi_cap",
        scheme_slug="hdfc_equity_flexi_cap_direct_growth",
        url="https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth",
    ),
    SourceSpec(
        scheme_name="HDFC ELSS Tax Saver Fund - Direct Growth",
        scheme_category="elss",
        scheme_slug="hdfc_elss_tax_saver_direct_growth",
        url="https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
    ),
    SourceSpec(
        scheme_name="HDFC Small Cap Fund - Direct Growth",
        scheme_category="small_cap",
        scheme_slug="hdfc_small_cap_direct_growth",
        url="https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth",
    ),
    SourceSpec(
        scheme_name="HDFC Balanced Advantage Fund - Direct Growth",
        scheme_category="balanced_advantage",
        scheme_slug="hdfc_balanced_advantage_direct_growth",
        url="https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth",
    ),
]

SCHEME_URLS: list[str] = [spec.url for spec in SOURCE_URLS]

SUPPORTED_SCHEMES: dict[str, str] = {
    spec.scheme_name.lower(): spec.scheme_name for spec in SOURCE_URLS
}
"""Lowercase scheme name -> display name, for out-of-scope detection (guards) and
for the "I only cover these schemes" message."""


SCHEME_PATTERNS: tuple[tuple[str, str], ...] = (
    ("large_cap", r"\blarge[\s-]?cap\b"),
    ("flexi_cap", r"\bflexi[\s-]?cap\b"),
    ("elss", r"\belss\b|\btax[\s-]?saver\b"),
    ("small_cap", r"\bsmall[\s-]?cap\b"),
    ("balanced_advantage", r"\bbalanced[\s-]?advantage\b"),
)
"""Words that name one of the five schemes, matched against the raw question.

Used by `retrieve` to scope a question that names exactly one scheme to that scheme's
own page. They are deliberately the *distinguishing* words only: "large cap" and "small
cap" are unique, whereas "equity" or "fund" are shared by several schemes and would
misfire. A question that names no scheme (or names two) matches nothing and is left
unfiltered, so comparative questions still see every page."""


SCHEME_DISPLAY_NAMES: dict[str, str] = {
    spec.scheme_category: spec.scheme_name for spec in SOURCE_URLS
}
"""scheme_category -> display name, for rewriting an elliptical follow-up into a
self-contained question (e.g. "its exit load" -> "the exit load for
HDFC Small Cap Fund - Direct Growth")."""


FOLLOWUP_MARKERS: tuple[str, ...] = (
    r"\bits\b",
    r"\bit\b",
    r"\bthis\b",
    r"\bthat\b",
    r"\bthese\b",
    r"\bthose\b",
    r"\bthere\b",
    r"\bthe same\b",
    r"\bthe above\b",
    r"\bsame (?:fund|scheme|one)\b",
    r"\A(?:and|also|ok|okay|now|then)\b",
    r"\Awhat about\b",
)
"""Anaphora that makes a question elliptical: it refers to the fund without naming it.

A question only inherits a scheme from the memory window when one of these is present.
Without this gate a self-contained question ("What is the minimum SIP?") could be
silently narrowed to whatever was asked about earlier in the session, which would hide
the other four funds from a user who never asked for a narrowing.

The last two patterns are anchored with `\\A` on purpose. A bare "And the minimum SIP?" has
no pronoun but is still a follow-up, whereas "the expense ratio and the exit load?" uses
the same word mid-sentence while naming no referent at all; anchoring tells those apart."""


COMPARATIVE_MARKERS: tuple[str, ...] = (
    r"\bvs\.?\b",
    r"\bversus\b",
    r"\bcompare[ds]?\b",
    r"\bcompared (?:to|with)\b",
    r"\bthan\b",
    r"\bdifference\b",
    r"\bwhich (?:one|of|is|has|do|does)\b",
    r"\bthe others?\b",
    r"\banother\b",
    r"\beach\b",
    r"\bhigher\b",
    r"\blower\b",
    r"\bbetter\b",
    r"\bworse\b",
    r"\bcheaper\b",
    r"\briskier\b",
    r"\bsafer\b",
    r"\bbest\b",
    r"\bworst\b",
)
"""Comparison words, used to keep a question from being narrowed to a single fund.

A comparison needs every page it refers to, so scoping one -- by a name the user typed
or by a referent inherited from history -- would hide the very fund being compared
against. This is the rule `_named_scheme`'s docstring has always claimed and now
actually applies.

Deliberately excludes bare "more", "less" and "most". They are too common to be evidence
of a comparison: "What is the most common risk in the large cap fund?" names one fund
and means it, and unscoping it would let a sibling fund's risk text answer. A real
comparison carries "than", "vs", "which one", or an explicit comparative, all of which are
listed above."""


# --------------------------------------------------------------------------
# Source allow-list
# --------------------------------------------------------------------------

ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {"groww.in", "hdfcamc.com", "amfiindia.com", "sebi.gov.in"}
)

EDUCATION_URLS: dict[str, str] = {
    "sebi_mutual_funds": "https://www.sebi.gov.in/sebiweb/other/mutualfunds.jsp",
    "sebi_understanding_mf": "https://investor.sebi.gov.in/understanding_mf.html",
    "amfi_investor_corner": "https://www.amfiindia.com/investor",
    "amfi_knowledge_centre": (
        "https://www.amfiindia.com/investor/knowledge-center-info"
        "?zoneName=IntroductionMutualFunds"
    ),
}
"""Investor-education pages, used only to redirect a refused advice or performance
question. They are not part of the answerable corpus and must never be cited as the
source of a fact."""

FACTSHEET_URL = "https://www.hdfcfund.com"
"""HDFC AMC's official website, where scheme factsheets are published. Used only as
the performance-refusal link (C3): the deflection message carries no numbers and this
link, never a citation for a fact. Verified to resolve (their edge returns 403 to bots,
which is expected; browsers open it normally)."""


def url_allowed(url: str) -> bool:
    """True if the URL's host is an allow-listed domain or a subdomain of one.

    Used by the ingest allow-list (C1) and by the citation validator. Matching is
    exact-host-or-subdomain so that `groww.in.evil.example` and `notgroww.in` are
    both rejected, while `www.groww.in` and `investor.sebi.gov.in` are accepted.
    """
    if not url:
        return False
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if not host:
        return False
    return any(
        host == domain or host.endswith(f".{domain}") for domain in ALLOWED_DOMAINS
    )


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_tokenizer():
    """The real embedder tokenizer, for asserting chunk token counts.

    Deliberately lazy and cached: importing sentence-transformers or transformers at
    module import time would make `import config` slow and would fail on a machine
    where the model is not installed yet.

    Tries the local cache first. Once the model has been downloaded (or on any machine
    that already has it), this avoids the hub's metadata HEAD-check entirely, which can
    otherwise stall for minutes on a flaky network and hang the demo. Falls back to a
    normal network load when the model is not cached yet (first install).
    """
    from transformers import AutoTokenizer

    try:
        return AutoTokenizer.from_pretrained(EMBED_MODEL, local_files_only=True)
    except OSError:
        return AutoTokenizer.from_pretrained(EMBED_MODEL)


def embedding_model_id() -> str:
    return EMBED_MODEL


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def _summary() -> str:
    dotenv_state = "loaded" if DOTENV_AVAILABLE else "not installed"
    key_state = "present" if api_key_present() else "absent"
    lines = [
        "config summary",
        f"  embed model        : {EMBED_MODEL} ({EMBED_DIM}-dim, "
        f"max {MAX_EMBED_TOKENS} tokens)",
        f"  chroma             : {CHROMA_PATH} :: {COLLECTION}",
        f"  raw dir            : {RAW_DIR}",
        f"  chunks file        : {CHUNKS_TXT}",
        f"  chunking           : target {CHUNK_TARGET_WORDS}w / max "
        f"{CHUNK_MAX_WORDS}w / overlap {CHUNK_OVERLAP_PCT:.0%} / "
        f"min {CHUNK_MIN_WORDS}w",
        f"  retrieval          : top_k {TOP_K} / floor {SCORE_FLOOR} / "
        f"mmr {MMR_LAMBDA} / aliases {len(QUERY_ALIASES)}",
        f"  schemes            : {len(SOURCE_URLS)}",
        f"  allowed domains    : {', '.join(sorted(ALLOWED_DOMAINS))}",
        f"  education links    : {len(EDUCATION_URLS)} (refusals only)",
        f"  python-dotenv      : {dotenv_state}",
        f"  GROQ_API_KEY       : {key_state}",
    ]
    for spec in SOURCE_URLS:
        lines.append(f"    - [{spec.scheme_category}] {spec.scheme_name}")
        lines.append(f"      {spec.url}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(_summary())
