"""Tests for the retrieval stage, against the real Chroma collection.

These are integration tests: they embed with the real `all-MiniLM-L6-v2` and search the
real collection built by `python -m ingest`. That is deliberate — the failure mode this
stage must catch is *silent*: a mismatched embedder, a chunk that never got indexed, or a
score floor that drops a legitimate core fact. A fake collection cannot express any of
those. They stay offline: no network and no `GROQ_API_KEY`, so `pytest -q` is green with
no key set (Phase 9 exit gate).

Run `python -m ingest --cache-only` first if `chroma_db/` is empty.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

import config
import guards
from ingest import open_collection
from retrieve import (
    Hit,
    RetrievalResult,
    _carried_scheme,
    _expand_query,
    _named_scheme,
    build_context,
    mmr,
    resolve_question,
    retrieve,
)

CORE_FACTS = (
    ("expense ratio", "What is the expense ratio?", "Charges and fees"),
    ("exit load", "What is the exit load?", "Charges and fees"),
    ("minimum SIP", "What is the minimum SIP investment?", "Investment limits"),
    ("ELSS lock-in", "What is the lock-in period for the ELSS fund?", "Investment limits"),
    ("riskometer", "What is the riskometer of the fund?", "Risk and benchmark"),
    ("benchmark", "What is the benchmark index?", "Risk and benchmark"),
)
"""(name, question, heading the top hit must carry). Each of the six core facts named in
`config.CORE_FACT_KEYS` has to be reachable by a question a user would actually type."""

JUNK_QUESTIONS = (
    "asdfghjkl nonsense query",
    "qqqq zzzz wwww",
    "!!! ??? ***",
    "the mitochondria is the powerhouse of the cell",
)
"""In-corpus vocabulary is absent, so none of these should clear the floor. The last one
is the interesting case: it is fluent English that simply is not about these funds."""


@pytest.fixture(scope="session", autouse=True)
def collection_is_built():
    """Fail with an actionable message instead of an empty-collection false negative."""
    if open_collection().count() == 0:
        pytest.fail(
            "Chroma collection is empty. Run `python -m ingest --cache-only` "
            "(no network) before running the retrieval tests."
        )


# ---------------------------------------------------------------------------
# Core-fact coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,question,heading", CORE_FACTS)
def test_core_fact_is_confident_and_lands_in_the_right_section(name, question, heading):
    """Phase 9's coverage requirement: every core fact is retrievable, above the floor,
    in the section that actually states it — not merely the top hit of *something*."""
    result = retrieve(question)
    assert result.confident, name
    assert result.hits, name
    top = result.hits[0]
    assert top.similarity >= config.SCORE_FLOOR, name
    assert top.metadata["heading_path"] == heading, name
    assert result.newest_fetched_at == max(h.metadata["fetched_at"] for h in result.hits)


def test_lock_in_query_finds_the_lock_in_text():
    """The terse-query failure this project hit: "Exit load?" scored 0.12 unexpanded and
    fell below the floor. The fix is the alias table, so assert on the text, not just
    confidence — a confident hit that never mentions the lock-in is still a miss."""
    result = retrieve("What is the lock-in period for the ELSS fund?")
    assert result.confident
    top = result.hits[0]
    assert top.metadata["source_url"] == (
        "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth"
    )
    assert re.search(r"lock[- ]?in", top.document, re.I), top.document


# ---------------------------------------------------------------------------
# Score floor / junk
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", JUNK_QUESTIONS)
def test_junk_query_is_not_confident(question):
    """The `confident=False` path is what routes the app to `not_in_sources` instead of
    hallucinating (FR-16). Junk must never reach the LLM."""
    result = retrieve(question)
    assert result.confident is False
    assert result.hits == []
    assert result.newest_fetched_at == ""


def test_confident_implies_every_hit_clears_the_floor():
    result = retrieve("What is the expense ratio?")
    for hit in result.hits:
        assert hit.similarity >= config.SCORE_FLOOR


# ---------------------------------------------------------------------------
# Scheme routing: no supported scheme is lost or misrouted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", config.SOURCE_URLS, ids=lambda s: s.scheme_category)
def test_supported_scheme_is_allowed_and_top_hit_is_its_own_page(spec):
    """All five schemes share the labels "Expense ratio" and "Exit load", so the real
    risk is not refusing a supported fund but answering with a sibling fund's fee. The
    question must pass the guards, and its own page must be ranked first."""
    question = f"What is the expense ratio of {spec.scheme_name}?"
    assert guards.run_guards(question).action == "allow"

    result = retrieve(question)
    assert result.confident, spec.scheme_slug
    assert result.hits[0].metadata["source_url"] == spec.url, spec.scheme_slug
    assert result.hits[0].metadata["scheme_name"] == spec.scheme_name


def test_no_supported_scheme_name_trips_the_out_of_corpus_rules():
    """`guards._OTHER_HOUSES_RE` and `_OTHER_ASSET_RE` are substring searches over a fixed
    vocabulary, so a supported scheme could in principle be swallowed by one. Every name
    must classify ALLOWED on its own."""
    for name in config.SUPPORTED_SCHEMES.values():
        assert guards.classify_intent(name) == "ALLOWED", name


# ---------------------------------------------------------------------------
# Scheme routing: a named scheme stays on its own page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,category",
    (
        ("Who is the fund manager of the large cap fund?", "large_cap"),
        ("Who manages the small cap fund?", "small_cap"),
        ("What is the lock-in period for the ELSS fund?", "elss"),
        ("What is the expense ratio of the tax saver fund?", "elss"),
        ("What is the benchmark of the balanced advantage fund?", "balanced_advantage"),
        ("Exit load of the flexi cap fund?", "flexi_cap"),
    ),
)
def test_named_scheme_keeps_every_hit_on_that_scheme(question, category):
    """When a question names exactly one scheme, no other scheme's chunk may survive.

    This is the failure that made "who manages hdfc large cap" unanswerable: the large-cap
    `Scheme identity` chunk was ranked 3rd at 0.690, above the small-cap one at 0.679, but
    MMR scored it down for resembling the other large-cap sections already selected and
    dropped it, leaving the context holding the *wrong* fund's manager. Asserting on the
    whole hit set, not just the top hit, is what pins that regression down.
    """
    result = retrieve(question)
    assert result.confident, question
    assert result.hits, question
    assert {h.metadata["scheme_category"] for h in result.hits} == {category}, question


@pytest.mark.parametrize(
    "question",
    (
        "What is the expense ratio?",
        "What is the benchmark index?",
        "Is the exit load higher on the small cap or the large cap?",
    ),
)
def test_unscoped_and_multi_scheme_questions_are_not_filtered(question):
    """Zero or two named schemes must leave the candidate pool alone, or a comparative
    question loses the other page it needs and a scheme-less question collapses to one
    arbitrary fund."""
    assert _named_scheme(question) is None, question
    result = retrieve(question)
    assert result.confident, question
    assert len({h.metadata["scheme_category"] for h in result.hits}) > 1, question


def test_fund_manager_is_retrievable_however_it_is_phrased():
    """The fact is in every page's `Scheme identity` chunk; only the wording varied. A
    question a user would plausibly type must surface the manager line in the context,
    not merely land on *some* chunk of the right page."""
    for question in (
        "Who is the fund manager of the large cap fund?",
        "Who manages the small cap fund?",
        "who manages hdfc large cap",
        "manager of the balanced advantage fund",
        "who is in charge of the ELSS fund?",
        "managed by whom is the flexi cap fund",
    ):
        result = retrieve(question)
        assert result.confident, question
        managers = [
            line
            for hit in result.hits
            for line in hit.document.splitlines()
            if line.startswith("Fund manager:")
        ]
        assert managers, f"{question!r} retrieved no Fund manager line"


# ---------------------------------------------------------------------------
# Multi-turn memory window (FR-18)
# ---------------------------------------------------------------------------

FOLLOWUP_HISTORY = [
    "What is the expense ratio of the HDFC Large Cap Fund - Direct Growth?",
    "Who manages the small cap fund?",
]
"""A two-turn window whose most recent scheme is small_cap. Resolving "its" must land on
small_cap, not on the older large_cap turn — the newest mention is the live referent."""


@pytest.mark.parametrize(
    "question,heading",
    (
        ("What is its exit load?", "Charges and fees"),
        ("Is it risky?", "Risk and benchmark"),
        ("What is the riskometer for it?", "Risk and benchmark"),
        ("And the minimum SIP?", "Investment limits"),
        ("What about the benchmark?", "Risk and benchmark"),
    ),
)
def test_elliptical_follow_up_resolves_to_the_referred_scheme(question, heading):
    """FR-18's acceptance case: a follow-up that names no fund must still answer about the
    fund the session is already on, and about the section that states the fact."""
    result = retrieve(question, history=FOLLOWUP_HISTORY)
    assert result.confident, question
    assert {h.metadata["scheme_category"] for h in result.hits} == {"small_cap"}, question
    assert result.hits[0].metadata["heading_path"] == heading, question


def test_a_carried_referent_answers_a_manager_follow_up_from_the_right_fund():
    """Who manages it? after a small-cap turn must surface that fund's manager line.

    Asserted on the line appearing in the context, not on it being the top hit: rewriting
    the question to name the fund pulls that page's `Scheme objective` chunk (which also
    carries the fund name) to the top, and `Scheme identity` lands second. The answer still
    has the manager in front of the model, which is what matters."""
    result = retrieve("Who manages it?", history=FOLLOWUP_HISTORY)
    assert result.confident
    assert {h.metadata["scheme_category"] for h in result.hits} == {"small_cap"}
    managers = [
        line
        for hit in result.hits
        for line in hit.document.splitlines()
        if line.startswith("Fund manager:")
    ]
    assert managers, "carried follow-up retrieved no Fund manager line"


def test_most_recent_named_scheme_wins_over_an_older_one():
    """Both turns name a different fund; the newest is the one the user is still on."""
    result = retrieve("What is its expense ratio?", history=FOLLOWUP_HISTORY)
    assert {h.metadata["scheme_category"] for h in result.hits} == {"small_cap"}


def test_history_changes_nothing_for_a_question_with_no_referent():
    """A self-contained question must not be narrowed by whatever came earlier, or a user
    who never asked for a narrowing silently loses the other four funds."""
    result = retrieve("What is the minimum SIP?", history=FOLLOWUP_HISTORY)
    assert len({h.metadata["scheme_category"] for h in result.hits}) > 1


def test_a_scheme_named_in_the_question_overrides_the_carried_one():
    """An explicit name is not a guess, so it always beats the inherited referent."""
    assert _carried_scheme(
        "What is the expense ratio of the ELSS fund?", FOLLOWUP_HISTORY
    ) is None
    result = retrieve("What is the expense ratio of the ELSS fund?", history=FOLLOWUP_HISTORY)
    assert {h.metadata["scheme_category"] for h in result.hits} == {"elss"}


@pytest.mark.parametrize(
    "question",
    (
        "Is its exit load higher than the other funds'?",
        "Which one has the higher expense ratio?",
        "Is its expense ratio higher than the others?",
    ),
)
def test_comparative_follow_up_is_never_narrowed_to_the_guessed_fund(question):
    """A comparison needs the other funds' pages too. Scoping an inherited referent onto a
    comparison would hide the very funds being compared against.

    These are the cases where nothing is named explicitly, so no other path can scope them:
    a comparison that does name a fund is scoped by that name (see
    `test_a_comparative_naming_a_scheme_is_scoped_by_that_name_not_by_memory`)."""
    assert _carried_scheme(question, FOLLOWUP_HISTORY) is None, question
    result = retrieve(question, history=FOLLOWUP_HISTORY)
    assert result.confident, question
    assert len({h.metadata["scheme_category"] for h in result.hits}) > 1, question


@pytest.mark.parametrize(
    "question",
    (
        "Is its exit load higher than the large cap's?",
        "What about the ELSS fund - is it better?",
        "Is the small cap fund's exit load higher than the ELSS fund's?",
    ),
)
def test_a_comparative_naming_a_scheme_is_not_narrowed_to_it(question):
    """Naming one fund used to be enough to scope, so a one-named comparison was answered
    from that single page and presented as a comparison. `_named_scheme` documented the
    opposite behaviour from the start; this pins the fix.

    Asserted on the *filter*, not on which pages happen to rank high: a question that
    mentions only one fund can still fill the top 5 from that fund without anything having
    narrowed it."""
    assert _named_scheme(question) is None, question
    assert _carried_scheme(question, FOLLOWUP_HISTORY) is None, question


def test_a_comparison_spanning_two_funds_reaches_both():
    """The user-visible consequence: both funds' sections have to be available."""
    result = retrieve("Is the small cap fund's exit load higher than the ELSS fund's?")
    assert result.confident
    categories = {h.metadata["scheme_category"] for h in result.hits}
    assert {"small_cap", "elss"} <= categories, categories


@pytest.mark.parametrize(
    "question,category",
    (
        ("What is the most common risk in the large cap fund?", "large_cap"),
        ("Which manager of the ELSS fund handles the most assets?", "elss"),
        ("Is there a less risky option in the balanced advantage fund?",
         "balanced_advantage"),
        ("What are the more relevant risks for the flexi cap fund?", "flexi_cap"),
    ),
)
def test_comparison_words_that_are_not_comparisons_still_scope(question, category):
    """The guard against over-triggering. Bare "more", "less" and "most" once appeared in
    `COMPARATIVE_MARKERS`; they are far too common to mean "compare two funds", and
    treating them as comparisons would unscope ordinary single-fund questions and let a
    sibling fund's page answer."""
    assert _named_scheme(question) == category, question
    result = retrieve(question)
    assert result.confident, question
    assert {h.metadata["scheme_category"] for h in result.hits} == {category}, question


@pytest.mark.parametrize("question", JUNK_QUESTIONS)
def test_junk_is_still_rejected_when_a_memory_window_is_present(question):
    """The regression that decided the whole design.

    Appending history to the query text lets the prior turn's vocabulary dominate the query
    vector: "asdfghjkl nonsense query" plus this history measured 0.82 and came back
    `confident=True`, while the same question alone is correctly rejected at the floor.
    Referent resolution never touches the query, so the rejection has to survive."""
    assert _carried_scheme(question, FOLLOWUP_HISTORY) is None, question
    result = retrieve(question, history=FOLLOWUP_HISTORY)
    assert result.confident is False, question
    assert result.hits == [], question


def test_no_referent_in_the_window_leaves_the_question_unscoped():
    """History that never named a fund gives nothing to inherit."""
    result = retrieve(
        "What is its exit load?",
        history=["What is the expense ratio?", "What is the benchmark index?"],
    )
    assert len({h.metadata["scheme_category"] for h in result.hits}) > 1


def test_the_window_is_bounded_to_memory_turns():
    """A turn older than the window is forgotten, so a long session cannot change the
    answer for a question whose only referent has aged out."""
    assert config.MEMORY_TURNS == 10
    aged_out = ["What is the expense ratio of the large cap fund?"] + [
        f"unrelated question {i}" for i in range(config.MEMORY_TURNS)
    ]
    assert len(aged_out) == config.MEMORY_TURNS + 1
    assert _carried_scheme("What is its exit load?", aged_out) is None

    still_visible = aged_out[1:]
    assert _carried_scheme("What is its exit load?", still_visible) is None


def test_the_newest_ten_turns_are_the_ones_that_count():
    """A fund named inside the window is still remembered after ten later turns."""
    inside = ["What is the expense ratio of the large cap fund?"] + [
        f"unrelated question {i}" for i in range(config.MEMORY_TURNS - 1)
    ]
    assert len(inside) == config.MEMORY_TURNS
    assert _carried_scheme("What is its exit load?", inside) == "large_cap"


def test_resolve_question_rewrites_only_an_inherited_referent():
    """AQ-2: the model is shown a self-contained question, but only when one was actually
    inferred, and the scheme it inferred is reported alongside."""
    resolved, scheme = resolve_question("What is its exit load?", FOLLOWUP_HISTORY)
    assert scheme == "small_cap"
    assert config.SCHEME_DISPLAY_NAMES["small_cap"] in resolved

    unchanged, scheme = resolve_question("What is the expense ratio of the ELSS fund?")
    assert unchanged == "What is the expense ratio of the ELSS fund?"
    assert scheme == "elss"

    passthrough, scheme = resolve_question("What is the minimum SIP?", FOLLOWUP_HISTORY)
    assert passthrough == "What is the minimum SIP?"
    assert scheme is None


def test_a_carried_scheme_answers_from_its_own_page_url():
    """The citation has to name the fund the follow-up referred to, not a sibling."""
    result = retrieve("What is its expense ratio?", history=FOLLOWUP_HISTORY)
    small_cap = next(s for s in config.SOURCE_URLS if s.scheme_category == "small_cap")
    assert result.hits[0].metadata["source_url"] == small_cap.url


# ---------------------------------------------------------------------------
# Hit shape and MMR
# ---------------------------------------------------------------------------


def test_hits_are_capped_at_top_k_and_carry_all_metadata():
    result = retrieve("What is the expense ratio?")
    assert 0 < len(result.hits) <= config.TOP_K
    for hit in result.hits:
        assert isinstance(hit, Hit)
        assert hit.document.strip()
        for field in ("source_url", "page_title", "heading_path", "fetched_at",
                      "scheme_name", "scheme_category", "chunk_index"):
            assert hit.metadata.get(field) not in (None, ""), field
        assert config.url_allowed(hit.metadata["source_url"])


def test_mmr_returns_diverse_chunks():
    """MMR exists because plain top-5 returns five "Charges and fees" chunks. Assert no
    repeated (page, section) pair survives selection."""
    result = retrieve("What is the exit load?")
    keys = [(h.metadata["source_url"], h.metadata["heading_path"]) for h in result.hits]
    assert len(set(keys)) == len(keys), keys


def test_mmr_keeps_all_hits_when_k_exceeds_the_candidate_count():
    hits = [
        Hit(document="a", metadata={"fetched_at": "2026-01-01"}, similarity=0.9),
        Hit(document="b", metadata={"fetched_at": "2026-01-01"}, similarity=0.8),
    ]
    calls = []

    def embed(texts):
        calls.append(texts)
        raise AssertionError("must not embed when every candidate is selected")

    assert mmr(hits, k=5, lambda_=config.MMR_LAMBDA, embed_fn=embed) == hits
    assert calls == []


def test_mmr_prefers_the_diverse_chunk_over_a_near_duplicate():
    """Pure unit test, no embedder needed: the two duplicate chunks are near-identical
    vectors, so once one is chosen the other is penalised and the distinct chunk wins."""
    hits = [
        Hit(document="exit load", metadata={"fetched_at": "2026-01-01"}, similarity=0.50),
        Hit(document="exit load", metadata={"fetched_at": "2026-01-01"}, similarity=0.49),
        Hit(document="lock-in period", metadata={"fetched_at": "2026-01-01"}, similarity=0.30),
    ]
    # rows 0 and 1 are near-parallel (a duplicated section); row 2 is orthogonal.
    vectors = np.array([[0.0, 1.0, 0.0], [0.1, 0.99, 0.0], [1.0, 0.0, 0.0]])

    def embed(texts):
        return vectors

    selected = mmr(hits, k=2, lambda_=config.MMR_LAMBDA, embed_fn=embed)
    assert len(selected) == 2
    assert selected[0] is hits[0]  # highest query similarity is always first
    assert selected[1] is hits[2]  # the distinct chunk beats the near-duplicate


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,expected_tail",
    (
        ("What is the expense ratio?", "Expense ratio charges and fees"),
        ("Exit load?", "Exit load charges and fees"),
        ("Minimum SIP investment amount?", "Minimum SIP investment Investment limits"),
        ("Lock-in period?", "Lock-in period Investment limits"),
        ("Who is the fund manager?", "Fund manager Scheme identity"),
    ),
)
def test_alias_fires_for_known_topics(question, expected_tail):
    expanded = _expand_query(question)
    assert expanded.startswith(question)
    assert expanded == f"{question} {expected_tail}"


@pytest.mark.parametrize(
    "question",
    (
        "Who manages the large cap fund?",
        "who manages hdfc large cap",
        "manager of the small cap fund",
        "who is in charge of the ELSS fund?",
        "managed by whom is the flexi cap fund",
        "who runs the balanced advantage fund",
    ),
)
def test_fund_manager_alias_covers_every_phrasing(question):
    """The pattern used to be the literal `fund\\s*manager`, so only questions that already
    contained that exact phrase were expanded. A phrasing like "who manages hdfc large cap"
    matched no alias at all and the `Scheme identity` chunk fell out of the top-5 — the
    assistant answered "could not find" for a fact that was in its own corpus."""
    expanded = _expand_query(question)
    assert expanded == f"{question} Fund manager Scheme identity", question


def test_fund_management_does_not_alias_to_the_fund_manager():
    """"Fund management" is how users ask about fund size, which is a different fact from
    the manager and is out of scope. Aliasing it to the `Scheme identity` chunk would
    answer a question nobody asked and crowd the real answer out of the context."""
    assert _expand_query("what is the fund management fee") == "what is the fund management fee"


@pytest.mark.parametrize("question", JUNK_QUESTIONS)
def test_junk_query_is_never_expanded(question):
    """The expansion is what lifts core facts over the floor; if it also fired on junk it
    would drag junk over the floor too. Junk must pass through byte-identical."""
    assert _expand_query(question) == question


def test_expansion_does_not_leak_into_the_question_sent_to_the_llm():
    """Expansion exists only to shape the query vector. `retrieve()` embeds the expansion
    but returns hits only — the caller keeps the user's own wording (config.QUERY_ALIASES)."""
    assert "Exit load charges and fees" not in "What is the exit load?"


# ---------------------------------------------------------------------------
# build_context
# ---------------------------------------------------------------------------


def test_build_context_numbers_sources_and_appends_fetch_stamp():
    result = retrieve("What is the expense ratio?")
    context, newest = build_context(result)
    assert newest == result.newest_fetched_at

    for i, hit in enumerate(result.hits, 1):
        assert f"[SOURCE {i}] {hit.metadata['page_title']}" in context
        assert f"URL: {hit.metadata['source_url']}" in context
        assert f"FETCHED: {hit.metadata['fetched_at']}" in context
        assert hit.document in context

    labels = re.findall(r"\[SOURCE (\d+)\]", context)
    assert labels == [str(i) for i in range(1, len(result.hits) + 1)]


def test_build_context_stamp_is_the_newest_of_the_selected_chunks():
    """C7: the `Last updated from sources:` line is code-appended, never LLM-written, so it
    must be exactly the max `fetched_at` — a stale or invented date breaks the guarantee."""
    result = retrieve("What is the exit load?")
    stamps = re.findall(r"^FETCHED: (.+)$", build_context(result)[0], re.M)
    assert stamps
    assert max(stamps) == result.newest_fetched_at


def test_build_context_handles_the_empty_result():
    """`retrieve()` short-circuits before MMR when nothing clears the floor; the empty
    case must not blow up, because the app calls this before deciding what to render."""
    context, newest = build_context(RetrievalResult(hits=[], confident=False))
    assert context == ""
    assert newest == ""
