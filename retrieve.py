"""Stage 2 retrieval: query embedding -> Chroma search -> score floor -> MMR -> context.

No LLM here. `answer.py` (Phase 7) consumes a `RetrievalResult`.

The five schemes have near-identical field labels ("Expense ratio", "Exit load"), so a
plain top-5 readily returns five chunks from the same page. Retrieval therefore
over-fetches `top_k * 3` neighbours and applies greedy Maximal Marginal Relevance to keep
the set diverse, which matters because answers are capped at a few sentences and cite one
source.

MMR has to be told *what* to diversify across, though. Every chunk of one page is
maximally redundant with every other chunk of that page, so on "who manages hdfc large
cap" the large-cap `Scheme identity` chunk — the correct answer, ranked 3rd at 0.690 —
was scored down for resembling the other large-cap sections and dropped in favour of the
*small-cap* `Scheme identity` chunk. The model then saw one fund manager and one fund's
name, and answered that the manager was not in the sources. Scoping to the single scheme
a question names (`_named_scheme`) removes the wrong-scheme chunks from the candidate
pool before MMR runs, so the right chunk cannot be crowded out by a rival scheme's.

Scoping moved from a post-filter to a Chroma `where` clause. Post-filtering a 15-chunk
candidate pool left a resolved follow-up with as few as 2 of its own scheme's 5 chunks
available for context; filtering in the database returns that scheme's chunks on their own
merits instead. The two approaches agree on *which* scheme answers a question, so this
widens the available context without changing any answer.

Chunks and queries must share the exact model instance: mismatched embedding spaces
degrade retrieval silently, so both embed through `ingest.load_embedder()`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import config
from ingest import load_embedder, open_collection


@dataclass
class Hit:
    document: str
    metadata: dict
    similarity: float


@dataclass
class RetrievalResult:
    hits: list[Hit]
    confident: bool
    newest_fetched_at: str = ""


def _expand_query(question: str) -> str:
    """Deterministic query expansion for the embedding only.

    Terse topic queries ("Exit load?") under-score against long field-chunks in this
    embedding space (Phase 5 measured 0.12 vs junk's 0.16; the floor could not separate
    them). When the question mentions a known corpus topic, its canonical labels and
    section heading are appended so the query vector is drawn to the right chunk. No
    alias fires for junk, so junk queries pass through untouched. The returned string is
    used only to embed the query;     the caller keeps the user's original question.
    """
    expanded = question
    for pattern, add in config.QUERY_ALIASES:
        if re.search(pattern, question, re.IGNORECASE):
            expanded += f" {add}"
    return expanded


def _embed_texts(texts: list[str]):
    """Encode with the single shared embedder, L2-normalized."""
    return load_embedder().encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )


def _mentions_any(question: str, patterns: tuple[str, ...]) -> bool:
    """True if the question matches any of `patterns`, case-insensitively."""
    return any(re.search(pattern, question, re.IGNORECASE) for pattern in patterns)


def _matched_schemes(question: str) -> list[str]:
    """Every scheme this question names, in SCHEME_PATTERNS order.

    The length matters as much as the value: "small cap vs large cap" matches two and so
    must stay unfiltered, which `_named_scheme` cannot express on its own.
    """
    return [
        category
        for category, pattern in config.SCHEME_PATTERNS
        if re.search(pattern, question, re.IGNORECASE)
    ]


def _named_scheme(question: str) -> str | None:
    """The one supported scheme this question names, or None if it cannot be scoped.

    Three things stop scoping:

    - it names zero schemes ("What is the expense ratio?") -- scoping would collapse the
      answer to one arbitrary fund out of five;
    - it names two or more ("small cap vs large cap") -- a comparison needs both pages;
    - it compares ("Is the exit load higher on the small-cap?") -- it names one fund, but
      the answer has to reach the others, and filtering to the named one hides them.

    The third case is why this consults `config.COMPARATIVE_MARKERS` and not just the
    match count. It was documented here from the start but never implemented: the name
    alone was enough to filter, so every one-named comparison was answered from a single
    page and quietly presented as a comparison. Scoping now happens only when the
    question is genuinely about one fund.
    """
    matched = _matched_schemes(question)
    if len(matched) != 1:
        return None
    if _mentions_any(question, config.COMPARATIVE_MARKERS):
        return None
    return matched[0]


def _carried_scheme(question: str, history: Sequence[str]) -> str | None:
    """The scheme an elliptical follow-up refers back to, or None (FR-18).

    Walks the memory window newest-first and returns the first turn that named exactly
    one scheme. History is *never* concatenated into the query text: measured, that lets
    the prior turn's vocabulary dominate the query vector, so a nonsense question with
    history attached scores 0.82 and is reported `confident=True` while the same question
    alone is correctly rejected at the score floor. Resolving the referent and filtering
    on it keeps that rejection intact.
    """
    if _matched_schemes(question):
        return None  # a name in the question always beats an inherited one
    if not _mentions_any(question, config.FOLLOWUP_MARKERS):
        return None  # self-contained: nothing to resolve
    if _mentions_any(question, config.COMPARATIVE_MARKERS):
        return None  # a comparison needs every page, not the guessed one
    for turn in reversed(list(history)[-config.MEMORY_TURNS :]):
        named = _named_scheme(turn)
        if named:
            return named
    return None


def resolve_question(question: str, history: Sequence[str] = ()) -> tuple[str, str | None]:
    """Return `(question_to_use, scheme)` for one turn (AQ-2).

    The question itself is only rewritten when a referent was actually inherited, so the
    stored text and the citation stay exactly what the user asked for. The rewrite names
    the fund, because "its exit load?" is ambiguous to the model even when retrieval has
    already been scoped to one page.
    """
    carried = _carried_scheme(question, history)
    if carried is None:
        return question, _named_scheme(question)
    name = config.SCHEME_DISPLAY_NAMES[carried]
    return f"{question.rstrip('?.! ')} for {name}?", carried


def retrieve(
    question: str,
    top_k: int = config.TOP_K,
    score_floor: float = config.SCORE_FLOOR,
    mmr_lambda: float = config.MMR_LAMBDA,
    history: Sequence[str] = (),
) -> RetrievalResult:
    """Rank, filter, and de-duplicate the context for one question.

    `history` is the bounded window of prior allowed questions (FR-18). It affects only
    which scheme's chunks are candidates; it never enters the query text.
    """
    resolved, scheme = resolve_question(question, history)
    q_vec = _embed_texts([_expand_query(resolved)])[0]
    collection = open_collection()
    query_kwargs = {"where": {"scheme_category": scheme}} if scheme else {}
    res = collection.query(
        query_embeddings=[q_vec.tolist()],
        n_results=top_k * 3,
        include=["documents", "metadatas", "distances"],
        **query_kwargs,
    )
    hits = [
        Hit(document=doc, metadata=meta, similarity=1.0 - dist)
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        )
    ]
    hits = [h for h in hits if h.similarity >= score_floor]
    if not hits:
        return RetrievalResult(hits=[], confident=False, newest_fetched_at="")

    selected = mmr(hits, top_k, mmr_lambda, _embed_texts)
    newest = max(h.metadata["fetched_at"] for h in selected)
    return RetrievalResult(hits=selected, confident=True, newest_fetched_at=newest)


def mmr(
    hits: list[Hit],
    k: int,
    lambda_: float,
    embed_fn,
) -> list[Hit]:
    """Greedy Maximal Marginal Relevance, choosing at most `k` hits.

    ChromaDB has no built-in MMR, and pulling in a vector-search framework for one
    knob would break A4, so this is a hand-rolled greedy pass. Each candidate is scored
    as:

        lambda * sim(query, candidate) - (1 - lambda) * max sim(candidate, selected)

    `Hit.similarity` is already the query-candidate cosine (query vectors are L2-
    normalized); the pairwise candidate similarities are recomputed with the same
    embedder (also normalized, so `vec @ vec.T` is cosine).
    """
    if k >= len(hits):
        return hits
    vectors = embed_fn([h.document for h in hits])
    pairwise = vectors @ vectors.T

    scores = [h.similarity for h in hits]
    remaining = set(range(len(hits)))
    chosen = [max(remaining, key=lambda i: scores[i])]
    remaining.remove(chosen[0])

    while remaining and len(chosen) < k:
        best_idx, best_score = -1, float("-inf")
        for i in remaining:
            max_selected_sim = max(pairwise[i][j] for j in chosen)
            score = lambda_ * scores[i] - (1.0 - lambda_) * max_selected_sim
            if score > best_score:
                best_idx, best_score = i, score
        chosen.append(best_idx)
        remaining.remove(best_idx)

    return [hits[i] for i in chosen]


def build_context(result: RetrievalResult) -> tuple[str, str]:
    """Assemble numbered [SOURCE n] blocks for the LLM, plus the newest fetch stamp.

    Returns `(context, newest_fetched_at)`. The `FETCHED` line is what makes the
    `Last updated from sources:` stamp honest: it is appended by code, never written by
    the LLM, so the model cannot invent a date (C7).
    """
    blocks = [
        f"[SOURCE {i}] {h.metadata['page_title']} — {h.metadata['heading_path']}\n"
        f"URL: {h.metadata['source_url']}\nFETCHED: {h.metadata['fetched_at']}\n"
        f"{h.document}"
        for i, h in enumerate(result.hits, 1)
    ]
    return "\n\n---\n\n".join(blocks), result.newest_fetched_at


def _probe() -> int:
    probes = [
        "What is the lock-in period for the ELSS fund?",
        "What is the exit load?",
        "asdfghjkl nonsense query",
    ]
    for question in probes:
        result = retrieve(question)
        if not result.confident:
            print(f"Q: {question}")
            print(f"  confident=False hits=0 (below score floor {config.SCORE_FLOOR})")
            continue
        top = result.hits[0]
        print(f"Q: {question}")
        print(f"  top    : {top.metadata['source_url']}")
        print(f"  heading: {top.metadata['heading_path']}")
        print(f"  sim    : {top.similarity:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_probe())