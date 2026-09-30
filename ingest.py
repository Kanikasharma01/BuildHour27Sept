"""Stage 1 of the RAG pipeline: fetch -> extract -> normalize.

This phase covers fetch, extract, and normalize only. Chunking, embedding, and
Chroma persistence land in Phases 3 and 4.

Extraction strategy, and why it is not the obvious one
------------------------------------------------------
These source pages are Next.js applications. The visible facts live in a
`<script id="__NEXT_DATA__">` JSON payload, not in the served HTML. A conventional
HTML extractor (trafilatura) recovers roughly 4,000 characters of main content from
a 450 KB page, and what it recovers is almost entirely performance tables: historic
returns, a category-rank table, and peer-comparison returns.

That content is exactly what PRD constraint C3 forbids - we must not state, compute,
or compare returns. So the facts are read from the JSON payload and whitelisted, and
the HTML prose path is retained only as a fallback for sources that carry no JSON,
where markdown tables are dropped outright because that is where the return data
lives.

The practical effect is that C3 is enforced structurally, at corpus build time, rather
than being left to a prompt instruction and an output validator at query time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import config

IST = timezone(timedelta(hours=5, minutes=30))

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)


# --------------------------------------------------------------------------
# Field schema
# --------------------------------------------------------------------------

FACT_GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "Identity",
        "Scheme identity",
        [
            ("fund_name", "Fund name"),
            ("scheme_name", "Scheme name"),
            ("plan_type", "Plan type"),
            ("scheme_type", "Option type"),
            ("amc", "AMC"),
            ("fund_house", "Fund house"),
            ("sub_category", "Category"),
            ("category", "Asset class"),
            ("launch_date", "Launch date"),
            ("allotment_date", "Allotment date"),
            ("face_value", "Face value"),
            ("fund_manager", "Fund manager"),
            ("registrar_agent", "Registrar and transfer agent"),
            ("isin", "ISIN"),
            ("scheme_code", "Scheme code"),
            ("direct_scheme_code", "Direct plan scheme code"),
            ("prod_code", "Product code"),
            ("closed_scheme", "Open for investment"),
        ],
    ),
    (
        "Charges",
        "Charges and fees",
        [
            ("expense_ratio", "Expense ratio"),
            ("expense_ratio_as_on", "Expense ratio as on"),
            ("base_expense_ratio", "Base expense ratio"),
            ("exit_load", "Exit load"),
            ("stamp_duty", "Stamp duty"),
        ],
    ),
    (
        "Limits",
        "Investment limits",
        [
            ("min_investment_amount", "Minimum lump sum investment"),
            ("min_sip_investment", "Minimum SIP investment"),
            ("mini_additional_investment", "Minimum additional investment"),
            ("min_withdrawal", "Minimum withdrawal"),
            ("sip_allowed", "SIP available"),
            ("lumpsum_allowed", "Lump sum available"),
            ("purchase_multiplier", "Purchase multiplier"),
            ("lock_in", "Lock-in period"),
            ("stp_flag", "STP available"),
            ("swp_flag", "SWP available"),
        ],
    ),
    (
        "Risk",
        "Risk and benchmark",
        [
            ("nfo_risk", "Riskometer"),
            ("benchmark", "Benchmark"),
            ("benchmark_name", "Benchmark index"),
            ("portfolio_turnover", "Portfolio turnover (percent, annual)"),
        ],
    ),
]

EXCLUDED_PERFORMANCE_FIELDS = frozenset(
    {
        "nav",
        "nav_date",
        "return_stats",
        "simple_return",
        "sip_return",
        "stats",
        "groww_rating",
        "aum",
        "peerComparison",
        "holdings",
        "analysis",
    }
)
"""Never ingested, as a hard boundary for PRD C3.

`nav` is excluded along with the return and rank fields so the corpus contains no
number the assistant could quote as a return or a comparison. `groww_rating` is a
proprietary score that reads as a recommendation, which PRD C4 excludes. `aum` is
excluded because the payload does not state its unit and an unlabelled magnitude
would be a guess. `holdings` and `peerComparison` are excluded as bulk data that
would dominate retrieval without answering any in-scope question.
"""


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class FetchedPage:
    url: str
    title: str
    scheme_name: str
    scheme_category: str
    source_type: str
    fetched_at: str
    text: str
    n_words: int
    status: str
    facts_found: list[str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.n_words >= config.MIN_WORDS_PER_SOURCE


@dataclass
class Chunk:
    """One retrievable unit. Every field is Chroma-metadata-safe (str/int/float/bool)."""

    text: str
    scheme_slug: str
    source_url: str
    page_title: str
    heading_path: str
    scheme_name: str
    scheme_category: str
    source_type: str
    chunk_index: int
    char_start: int
    char_end: int
    content_hash: str
    fetched_at: str

    @property
    def id(self) -> str:
        return f"{self.scheme_slug}::{self.chunk_index:03d}"

    @property
    def metadata(self) -> dict[str, str | int]:
        """Chroma metadata: every field but text and scheme_slug (the slug names the id)."""
        raw = {
            "source_url": self.source_url,
            "page_title": self.page_title,
            "heading_path": self.heading_path,
            "scheme_name": self.scheme_name,
            "scheme_category": self.scheme_category,
            "source_type": self.source_type,
            "chunk_index": self.chunk_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "content_hash": self.content_hash,
            "fetched_at": self.fetched_at,
        }
        return {k: (v if v is not None else "") for k, v in raw.items()}

    def __post_init__(self) -> None:
        assert_chunk_fits(self.text)


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------


def fetch_page(url: str, session=None) -> str:
    """GET a page, retrying with backoff. Raises RuntimeError on final failure."""
    import requests

    own = session is None
    session = session or requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    last: Exception | None = None
    try:
        for attempt in range(1, config.FETCH_RETRIES + 1):
            try:
                resp = session.get(url, timeout=config.FETCH_TIMEOUT)
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                return resp.text
            except Exception as exc:  # noqa: BLE001 - retried and reported below
                last = exc
                if attempt < config.FETCH_RETRIES:
                    time.sleep(config.FETCH_BACKOFF * attempt)
    finally:
        if own:
            session.close()
    raise RuntimeError(
        f"failed after {config.FETCH_RETRIES} attempts: {type(last).__name__}: {last}"
    )


# --------------------------------------------------------------------------
# Extract
# --------------------------------------------------------------------------


def extract_next_data(html: str) -> dict | None:
    """Return the scheme fact record from the page's __NEXT_DATA__ payload."""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
        record = payload["props"]["pageProps"]["mfServerSideData"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return record if isinstance(record, dict) and record else None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def format_lock_in(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    years, months, days = value.get("years"), value.get("months"), value.get("days")
    if not any([years, months, days]):
        return "No lock-in period"
    parts: list[str] = []
    for count, unit in ((years, "year"), (months, "month"), (days, "day")):
        if count:
            parts.append(f"{count} {unit}" + ("" if count == 1 else "s"))
    return " ".join(parts) or None


_INVERTED_BOOL_KEYS = frozenset({"closed_scheme"})
"""Source fields whose boolean sense is the opposite of their name.

`closed_scheme: false` means the scheme is open, so a direct bool render would emit
"Open for investment: No" for a live scheme.
"""


def format_value(key: str, value: object) -> str | None:
    """Render one JSON value as a single line of text, or None to skip it."""
    if value is None:
        return None
    if key == "lock_in":
        return format_lock_in(value)
    if key == "nfo_risk" and isinstance(value, str):
        text = _clean(value)
        return re.sub(r"\s+Riskometer$", "", text, flags=re.I) or None
    if isinstance(value, bool):
        if key in _INVERTED_BOOL_KEYS:
            value = not value
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = _clean(value)
        return text or None
    return None


def flatten_facts(record: dict) -> tuple[list[str], list[str]]:
    """Turn the fact record into `Field: Value` lines grouped under sections.

    Returns the lines and the list of core fact keys that were present, so ingest can
    assert coverage rather than trusting a word count.
    """
    fields = dict(record)
    latest = record.get("historic_fund_expense")
    if isinstance(latest, list) and latest:
        newest = max(
            (e for e in latest if isinstance(e, dict) and e.get("as_on_date")),
            key=lambda e: e["as_on_date"],
            default=None,
        )
        if newest:
            fields["expense_ratio_as_on"] = str(newest["as_on_date"])[:10]

    lines: list[str] = []
    for heading, title, pairs in FACT_GROUPS:
        block: list[str] = []
        for key, label in pairs:
            rendered = format_value(key, fields.get(key))
            if rendered:
                block.append(f"{label}: {rendered}")
        if block:
            lines.append(f"## {title}")
            lines.extend(block)
            lines.append("")

    found = [k for k in config.CORE_FACT_KEYS if fields.get(k) not in (None, "", {})]
    return lines, found


def extract_prose(html: str) -> str:
    """Fallback prose extraction for sources that carry no JSON fact record.

    Markdown tables are dropped unconditionally: on these pages the tables are the
    returns, category ranks, and peer comparisons that C3 forbids.
    """
    try:
        import trafilatura

        text = trafilatura.extract(
            html, output_format="markdown", include_tables=False, include_comments=False
        )
        if text:
            return text
    except Exception:  # noqa: BLE001 - fall through to BeautifulSoup
        pass
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(
        ["script", "style", "nav", "header", "footer", "aside", "noscript", "table"]
    ):
        tag.decompose()
    return soup.get_text(separator="\n")


# --------------------------------------------------------------------------
# Normalize
# --------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Collapse whitespace while preserving line structure and heading markers."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(_clean(line) for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_document(spec: config.SourceSpec, record: dict | None, prose: str) -> str:
    """Assemble the normalized page: identity header, fact sections, then prose."""
    parts = [f"# {spec.scheme_name}", ""]
    if record:
        fact_lines, _ = flatten_facts(record)
        parts.extend(fact_lines)
        description = record.get("description")
        if isinstance(description, str) and _clean(description):
            parts.extend(["## Scheme objective", _clean(description), ""])
    else:
        parts.extend(["## Page text", prose.strip(), ""])
    return normalize_text("\n".join(parts))


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

_FETCHED_AT_PREFIX = "# fetched_at: "


def parse_sections(text: str) -> list[tuple[str, list[str]]]:
    """Split a normalized document into (heading, body lines) by `##` markers.

    The `##` level is the section boundary: the chunking contract is that chunks never
    cross sections, because this corpus is a set of field records where each section is
    one coherent topic and every question targets a single topic.
    """
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in text.split("\n"):
        if line.startswith("## "):
            if current is not None:
                sections.append(current)
            current = (line[3:].strip(), [])
        elif current is not None and line.strip():
            current[1].append(line.strip())
    if current is not None:
        sections.append(current)
    return sections


def _word_count(text: str) -> int:
    return len(text.split())


def _title_of(text: str) -> str:
    for line in text.split("\n"):
        if line.startswith("# ") and not line.startswith(_FETCHED_AT_PREFIX):
            return line[2:].strip()
    return ""


def _fetched_at_of(text: str, fallback: str) -> str:
    for line in text.split("\n"):
        if line.startswith(_FETCHED_AT_PREFIX):
            return line[len(_FETCHED_AT_PREFIX) :].strip()
    return fallback


def assert_chunk_fits(text: str) -> None:
    """Raise if the real tokenizer would silently truncate this chunk.

    all-MiniLM-L6-v2 has max_seq_length 256; anything longer is truncated with no
    warning, so the chunk would lose its tail and the facts in it would be
    unretrievable. This runs on every chunk construction, not just once.
    """
    tokens = config.get_tokenizer()(text, add_special_tokens=False)["input_ids"]
    if len(tokens) > config.MAX_EMBED_TOKENS:
        raise ValueError(
            f"chunk is {len(tokens)} tokens, over the {config.MAX_EMBED_TOKENS}-token "
            f"model cap; section needs a splitter, which is a design change, not a "
            f"silent trim"
        )


def chunk_document(page: FetchedPage, text: str) -> list[Chunk]:
    """Chunk one normalized document, one chunk per section.

    Why the 200-word target and 20% overlap are not exercised here: the observed data
    (CHUNKING.md) has sections of 14-68 words, all far under CHUNK_MAX_WORDS, so no
    section needs spilling, and merging sections is forbidden by the no-cross-section
    rule because each section is a distinct retrieval topic. A section that ever
    exceeds the cap trips `assert_chunk_fits` or the check below instead of being
    silently spilled.
    """
    title = _title_of(text)
    fetched_at = _fetched_at_of(text, page.fetched_at)
    chunks: list[Chunk] = []
    for index, (heading, lines) in enumerate(parse_sections(text)):
        body = "\n".join(lines)
        if _word_count(body) < config.CHUNK_MIN_WORDS:
            continue
        if _word_count(body) > config.CHUNK_MAX_WORDS:
            raise ValueError(
                f"section {heading!r} is {_word_count(body)} words, over "
                f"{config.CHUNK_MAX_WORDS}; a splitter is a design decision, not an "
                f"auto-fix"
            )
        chunk_text = f"# {title}\n## {heading}\n{body}"
        start = text.find(body)
        chunks.append(
            Chunk(
                text=chunk_text,
                scheme_slug=page.scheme_category,
                source_url=page.url,
                page_title=title,
                heading_path=heading,
                scheme_name=page.scheme_name,
                scheme_category=page.scheme_category,
                source_type=page.source_type,
                chunk_index=index,
                char_start=start,
                char_end=start + len(body),
                content_hash=hashlib.sha1(chunk_text.encode("utf-8")).hexdigest(),
                fetched_at=fetched_at,
            )
        )
    return chunks


def write_chunks_txt(chunks: list[Chunk]) -> str:
    """Write every chunk to `data/chunks.txt`, human-readable, for inspection."""
    blocks: list[str] = []
    for chunk in chunks:
        lines = [
            f"=== CHUNK {chunk.chunk_index:03d} ===",
            f"source_url: {chunk.source_url}",
            f"page_title: {chunk.page_title}",
            f"heading_path: {chunk.heading_path}",
            f"scheme_name: {chunk.scheme_name}",
            f"scheme_category: {chunk.scheme_category}",
            f"source_type: {chunk.source_type}",
            f"chunk_index: {chunk.chunk_index}",
            f"char_start: {chunk.char_start}  char_end: {chunk.char_end}",
            f"content_hash: {chunk.content_hash}",
            f"fetched_at: {chunk.fetched_at}",
            f"tokens: {_token_count(chunk.text)}",
            "---",
            chunk.text,
            "",
        ]
        blocks.append("\n".join(lines))
    path = Path(config.CHUNKS_TXT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")
    return str(path)


def chunk_all(pages: list[FetchedPage]) -> list[Chunk]:
    return [chunk for page in pages if not page.error for chunk in chunk_document(page, page.text)]


# --------------------------------------------------------------------------
# Embedding & persistence (Phase 4)
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_embedder():
    """The sentence-transformers model, loaded exactly once per process.

    Tries the local cache first for the same reason as `config.get_tokenizer`: a
    from-hub load can stall on a flaky network's metadata HEAD-check at demo time. A
    missing cache still falls back to a real download (first install).

    The loaded model must not exceed MAX_EMBED_TOKENS; if it reported something
    different from the expected 256 we warn loudly and hard-fail when it is too big,
    because truncation is silent and loses facts (R8).
    """
    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(config.EMBED_MODEL, local_files_only=True)
    except OSError:
        model = SentenceTransformer(config.EMBED_MODEL)

    max_seq = getattr(model, "max_seq_length", None)
    if max_seq is None:
        print(
            "[warn] embedder reports no max_seq_length; "
            f"assuming {config.MAX_EMBED_TOKENS}"
        )
    elif max_seq > config.MAX_EMBED_TOKENS:
        raise ValueError(
            f"model {config.EMBED_MODEL} reports max_seq_length={max_seq}, over the "
            f"{config.MAX_EMBED_TOKENS} the chunker asserts against; chunks may be "
            f"silently truncated"
        )
    elif max_seq != config.MAX_EMBED_TOKENS:
        print(
            f"[warn] model max_seq_length={max_seq}, expected "
            f"{config.MAX_EMBED_TOKENS}. The chunker asserts the expected value; if "
            f"chunks are retokenized with this embedder they may truncate."
        )

    dim = (
        model.get_embedding_dimension()
        if hasattr(model, "get_embedding_dimension")
        else model.get_sentence_embedding_dimension()
    )
    if dim != config.EMBED_DIM:
        raise ValueError(
            f"model {config.EMBED_MODEL} is {dim}-dimensional, "
            f"expected {config.EMBED_DIM}"
        )
    return model


def embed_chunks(texts: list[str], batch_size: int = 32) -> np.ndarray:
    """Embed chunk texts -> (n, EMBED_DIM) float32, L2-normalized.

    Raises on a shape mismatch or an all-zero vector, because either means the model
    failed silently and every chunk after that point would be garbage.
    """
    import numpy as np

    vectors = load_embedder().encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != config.EMBED_DIM:
        raise ValueError(
            f"embedding produced shape {arr.shape}, expected (n, {config.EMBED_DIM})"
        )
    if np.any(np.all(arr == 0.0, axis=1)):
        raise ValueError("embedding produced an all-zero vector")
    return arr


def open_collection():
    """Chroma collection for facts.

    `embedding_function=None` is deliberate: Stage 1 embeds chunks itself with
    `load_embedder()` and passes vectors in explicitly. Handing Chroma a default
    embedding function would silently re-embed — with a different, network-dependent
    model — and turn every upsert into a second, uncontrolled embedding path.
    """
    import chromadb

    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    return client.get_or_create_collection(
        name=config.COLLECTION,
        metadata=config.COLLECTION_METADATA,
        embedding_function=None,
    )


def store_chunks(chunks: list[Chunk], vectors: np.ndarray) -> int:
    """Upsert every chunk by its deterministic id.

    Upsert, not add, so a re-run is idempotent: the same `{scheme_slug}::{index:03d}`
    id overwrites the same record instead of duplicating it. Returns the number of
    documents stored in this call.
    """
    collection = open_collection()
    collection.upsert(
        ids=[chunk.id for chunk in chunks],
        documents=[chunk.text for chunk in chunks],
        metadatas=[chunk.metadata for chunk in chunks],
        embeddings=vectors.tolist(),
    )
    return len(chunks)


def rebuild_collection() -> None:
    """Drop the collection entirely (--force-rebuild), so re-embedding starts clean."""
    import chromadb

    client = chromadb.PersistentClient(path=config.CHROMA_PATH)
    try:
        client.delete_collection(config.COLLECTION)
        print(f"deleted collection {config.COLLECTION!r}")
    except ValueError:
        print(f"collection {config.COLLECTION!r} did not exist; nothing to delete")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def load_page(spec: config.SourceSpec, cache_dir: Path, force: bool, session) -> FetchedPage:
    cache_file = cache_dir / f"{spec.scheme_slug}{config.RAW_SUFFIX}"
    now = datetime.now(IST).isoformat(timespec="seconds")

    def _result(html: str, status: str) -> FetchedPage:
        record = extract_next_data(html)
        prose = "" if record else extract_prose(html)
        text = build_document(spec, record, prose)
        text = f"{_FETCHED_AT_PREFIX}{now}\n{text}"
        cache_file.write_text(text, encoding="utf-8")
        _, found = flatten_facts(record) if record else ([], [])
        return FetchedPage(
            url=spec.url,
            title=(record or {}).get("scheme_name") or spec.scheme_name,
            scheme_name=spec.scheme_name,
            scheme_category=spec.scheme_category,
            source_type=spec.source_type,
            fetched_at=now,
            text=text,
            n_words=len(text.split()),
            status=status,
            facts_found=found,
        )

    try:
        if cache_file.exists() and not force:
            html = cache_file.read_text(encoding="utf-8")
            cached = FetchedPage(
                url=spec.url,
                title=spec.scheme_name,
                scheme_name=spec.scheme_name,
                scheme_category=spec.scheme_category,
                source_type=spec.source_type,
                fetched_at=_fetched_at_of(html, now),
                text=html,
                n_words=len(html.split()),
                status="cached",
                facts_found=[],
            )
            cached.facts_found = _facts_in_text(html)
            return cached
        return _result(fetch_page(spec.url, session), "fetched")
    except Exception as exc:  # noqa: BLE001 - collected and reported by main
        return FetchedPage(
            url=spec.url,
            title=spec.scheme_name,
            scheme_name=spec.scheme_name,
            scheme_category=spec.scheme_category,
            source_type=spec.source_type,
            fetched_at=now,
            text="",
            n_words=0,
            status="failed",
            facts_found=[],
            error=f"{type(exc).__name__}: {exc}",
        )


def _facts_in_text(text: str) -> list[str]:
    labels = {
        "expense_ratio": "Expense ratio",
        "exit_load": "Exit load",
        "min_sip_investment": "Minimum SIP investment",
        "lock_in": "Lock-in period",
        "nfo_risk": "Riskometer",
        "benchmark": "Benchmark",
    }
    return [key for key, label in labels.items() if f"{label}:" in text]


def fetch_all(
    pages: list[config.SourceSpec] | None = None,
    cache_dir: str | Path = config.RAW_DIR,
    force: bool = False,
    cache_only: bool = False,
) -> list[FetchedPage]:
    import requests

    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    pages = pages if pages is not None else config.SOURCE_URLS
    session = requests.Session()

    try:
        results: list[FetchedPage] = []
        for spec in pages:
            if cache_only:
                cached_file = cache / f"{spec.scheme_slug}{config.RAW_SUFFIX}"
                if not cached_file.exists():
                    results.append(
                        FetchedPage(
                            url=spec.url,
                            title=spec.scheme_name,
                            scheme_name=spec.scheme_name,
                            scheme_category=spec.scheme_category,
                            source_type=spec.source_type,
                            fetched_at=datetime.now(IST).isoformat(timespec="seconds"),
                            text="",
                            n_words=0,
                            status="missing",
                            facts_found=[],
                            error="no cache file and --cache-only was set",
                        )
                    )
                    continue
            results.append(load_page(spec, cache, force, session))
    finally:
        session.close()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ingest", description="Stage 1: fetch, extract, normalize, chunk."
    )
    parser.add_argument(
        "--force", action="store_true", help="refetch even if a cache file exists"
    )
    parser.add_argument(
        "--cache-only", action="store_true", help="never touch the network"
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help=(
            "chunk from cache only, write data/chunks.txt, print the chunk report, "
            "and stop before any embedding"
        ),
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="delete the Chroma collection before embedding (clean re-ingest)",
    )
    args = parser.parse_args(argv)

    results = fetch_all(force=args.force, cache_only=args.cache_only or args.inspect)

    print(f"{'category':<20} {'status':<9} {'words':>6}  core facts  url")
    print("-" * 100)
    for r in results:
        missing = set(config.CORE_FACT_KEYS) - set(r.facts_found)
        fact_cell = (
            f"{len(r.facts_found)}/{len(config.CORE_FACT_KEYS)}"
            if not missing
            else f"MISSING {','.join(sorted(missing))}"
        )
        print(f"{r.scheme_category:<20} {r.status:<9} {r.n_words:>6}  {fact_cell}")
        if r.error:
            print(f"{'':<20} error: {r.error}")
        print(f"{'':<20} -> {r.url}")

    thin = [r for r in results if r.error is None and r.n_words < config.MIN_WORDS_PER_SOURCE]
    failed = [r for r in results if r.error is not None]
    incomplete = [
        r
        for r in results
        if not r.error and set(config.CORE_FACT_KEYS) - set(r.facts_found)
    ]

    print()
    if failed:
        print(f"FAILED {len(failed)}/{len(results)}: " + ", ".join(r.scheme_category for r in failed))
    if thin:
        print(
            f"THIN {len(thin)}/{len(results)} (under {config.MIN_WORDS_PER_SOURCE} words): "
            + ", ".join(r.scheme_category for r in thin)
        )
    if incomplete:
        print(
            f"INCOMPLETE core facts on {len(incomplete)}: "
            + ", ".join(r.scheme_category for r in incomplete)
        )
        return 1
    if failed or thin:
        return 1

    chunks = chunk_all(results)
    chunks_txt = write_chunks_txt(chunks)
    token_counts = [_token_count(chunk.text) for chunk in chunks]
    chunk_words = [len(chunk.text.split()) for chunk in chunks]
    print(
        f"OK all {len(results)} sources; {len(chunks)} chunks written to "
        f"{chunks_txt}"
    )
    print(
        f"chunk sizes: {min(token_counts)}..{max(token_counts)} tokens "
        f"(model cap {config.MAX_EMBED_TOKENS}); "
        f"{min(chunk_words)}..{max(chunk_words)} words"
    )
    print(f"  per source: " + ", ".join(f"{cat}={n}" for cat, n in _count_per_source(results, chunks)))

    if args.inspect:
        print("--inspect: stopping before embedding (no Chroma writes).")
        return 0

    if args.force_rebuild:
        rebuild_collection()

    chunk_texts = [chunk.text for chunk in chunks]
    vectors = embed_chunks(chunk_texts)
    stored = store_chunks(chunks, vectors)
    collection = open_collection()
    print(
        f"persisted: chunks produced={len(chunks)} stored={stored} "
        f"collection.count()={collection.count()} "
        f"max token count={max(token_counts)}"
    )
    return 0


def _token_count(text: str) -> int:
    tokens = config.get_tokenizer()(text, add_special_tokens=False)["input_ids"]
    return len(tokens)


def _count_per_source(results: list[FetchedPage], chunks: list[Chunk]) -> list[tuple[str, int]]:
    by_slug: dict[str, int] = {}
    for chunk in chunks:
        by_slug[chunk.scheme_category] = by_slug.get(chunk.scheme_category, 0) + 1
    return [(r.scheme_category, by_slug.get(r.scheme_category, 0)) for r in results if not r.error]


if __name__ == "__main__":
    sys.exit(main())
