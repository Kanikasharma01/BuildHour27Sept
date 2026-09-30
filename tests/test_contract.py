"""Contract tests for answer.py. The LLM is faked by monkeypatching call_groq, so
these tests run with no API key and no network."""

import re

import pytest

import answer
import config
import guards
from answer import (
    DEGRADED_NOTICE,
    Answer,
    answer_question,
    call_groq,
    count_sentences,
    extractive_fallback,
    validate_answer,
)
from retrieve import Hit, RetrievalResult


def _hit(url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth", text=""):
    metadata = {
        "source_url": url,
        "page_title": "HDFC Large Cap Fund - Direct Growth",
        "heading_path": "Charges and fees",
        "scheme_name": "HDFC Large Cap Fund - Direct Growth",
        "scheme_category": "large_cap",
        "source_type": "scheme_page",
        "chunk_index": 1,
        "char_start": 1,
        "char_end": 2,
        "content_hash": "abc",
        "fetched_at": "2026-09-28T20:18:28+05:30",
    }
    return Hit(
        document=text or "## Charges and fees\nExpense ratio: 1.03\nExit load: Exit load of 1% if redeemed within 1 year",
        metadata=metadata,
        similarity=0.8,
    )


HITS = [_hit()]


def test_count_sentences_decimal_safe():
    assert count_sentences("The expense ratio is 1.16% p.a. Exit load is nil.") == 2


def test_good_answer_passes():
    raw = (
        "The expense ratio of HDFC Large Cap Fund - Direct Growth is 1.03%. "
        "There is an exit load of 1% if redeemed within 1 year.\n"
        "Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )
    ok, violations = validate_answer(raw, HITS)
    assert ok, violations


def test_four_sentence_answer_rejected():
    raw = (
        "One. Two. Three. Four.\n"
        "Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )
    ok, violations = validate_answer(raw, HITS)
    assert not ok
    assert any("sentences" in v for v in violations)


def test_zero_urls_rejected():
    ok, _ = validate_answer("The expense ratio is 1.03%.", HITS)
    assert not ok


def test_two_urls_rejected():
    raw = (
        "The expense ratio is 1.03%.\n"
        "Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth\n"
        "Also: https://www.amfiindia.com/"
    )
    ok, violations = validate_answer(raw, HITS)
    assert not ok
    assert any("URL" in v for v in violations)


def test_non_allowlisted_url_rejected():
    raw = "Answer.\nSource: https://evil.example/x"
    ok, violations = validate_answer(raw, HITS)
    assert not ok
    assert any("allow-list" in v for v in violations)


def test_url_not_in_hits_rejected():
    raw = "Answer.\nSource: https://groww.in/mutual-funds/other-fund-direct-growth"
    ok, violations = validate_answer(raw, HITS)
    assert not ok
    assert any("retrieved" in v for v in violations)


def test_performance_claim_rejected_but_index_name_ok():
    raw_perf = "It returned 12.5% in a year.\nSource: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    ok, violations = validate_answer(raw_perf, HITS)
    assert not ok
    assert any("performance" in v for v in violations)

    raw_index = (
        "Benchmark: NIFTY 500 Total Return Index.\n"
        "Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )
    ok, violations = validate_answer(raw_index, HITS)
    assert ok, violations


def test_pii_echo_rejected():
    raw = (
        "The folio is ABCDE1234F.\n"
        "Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )
    ok, violations = validate_answer(raw, HITS)
    assert not ok
    assert any("personal" in v for v in violations)


# ---------------------------------------------------------------------------
# Orchestration with a faked LLM
# ---------------------------------------------------------------------------


def _good_response(*args, **kwargs):
    return (
        "The expense ratio is 1.03%.\n"
        "Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )


@pytest.fixture
def canned_retrieval(monkeypatch):
    """Replace live retrieval (embedder + collection) with one fake confident hit."""

    def _patch(confident=True):
        result = RetrievalResult(
            hits=HITS,
            confident=confident,
            newest_fetched_at=HITS[0].metadata["fetched_at"],
        )
        monkeypatch.setattr(answer, "retrieve", lambda q, **kwargs: result)
        return result

    return _patch


def test_generated_answer_path(monkeypatch, canned_retrieval):
    canned_retrieval()
    monkeypatch.setattr(answer, "call_groq", _good_response)
    result = answer_question("What is the expense ratio?")
    assert result.mode == "generated"
    assert result.source_url == HITS[0].metadata["source_url"]
    assert result.source_title == HITS[0].metadata["page_title"]
    assert result.notice is None
    assert result.last_updated == HITS[0].metadata["fetched_at"]
    assert result.last_updated in result.text  # timestamp is code-appended
    assert result.text.rstrip().endswith(result.last_updated)  # ...as the last line


def test_repair_once_then_fallback(monkeypatch, canned_retrieval):
    """First answer violates (four sentences); repair also violates; -> extractive."""
    canned_retrieval()
    calls = {"n": 0}

    def fake(question, context, violations=None):
        calls["n"] += 1
        if calls["n"] == 1:
            assert violations is None
            return "One. Two. Three. Four.\nSource: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
        assert violations  # second call carries the violations
        return (
            "Still too long. Still too long. Still too long. Still too long.\n"
            "Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
        )

    monkeypatch.setattr(answer, "call_groq", fake)
    result = answer_question("What is the expense ratio?")
    assert calls["n"] == 2  # repaired exactly once
    assert result.mode == "extractive"


def test_repair_succeeds(monkeypatch, canned_retrieval):
    canned_retrieval()
    calls = {"n": 0}

    def fake(question, context, violations=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return "One. Two. Three. Four.\nSource: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
        return _good_response(question, context)

    monkeypatch.setattr(answer, "call_groq", fake)
    result = answer_question("What is the expense ratio?")
    assert calls["n"] == 2
    assert result.mode == "generated"


def test_degraded_mode_when_groq_fails(monkeypatch, canned_retrieval):
    canned_retrieval()
    monkeypatch.setattr(answer, "call_groq", lambda *a, **k: None)
    result = answer_question("What is the exit load?")
    assert result.mode == "extractive"
    assert result.notice == DEGRADED_NOTICE
    assert result.text.startswith("From the source page:")
    assert result.last_updated == HITS[0].metadata["fetched_at"]
    assert "Source: " in result.text


@pytest.mark.parametrize("blank", ("", "   ", "\n\n", None))
def test_a_blank_model_reply_is_a_failure_not_an_answer(monkeypatch, canned_retrieval, blank):
    """`call_groq` returns `""` when a reasoning model overruns `max_tokens` on its
    reasoning and Groq comes back with empty `content`. That is the same failure as `None`,
    so it must take the same path: degrade immediately with the notice.

    Treating it as an answer instead made it fail validation for the wrong reason
    ("exactly one URL, found 0"), spend the single repair attempt on a retry that was
    equally doomed, and then blame the provider in a notice."""
    canned_retrieval()
    calls = []

    def count(*args, **kwargs):
        calls.append(1)
        return blank

    monkeypatch.setattr(answer, "call_groq", count)
    result = answer_question("What is the exit load?")
    assert result.mode == "extractive"
    assert result.notice == DEGRADED_NOTICE
    assert len(calls) == 1, "a blank reply must not consume the repair attempt"


def test_usable_rejects_blank_and_none():
    assert answer._usable("a real answer") is True
    for blank in (None, "", "   ", "\n\t "):
        assert answer._usable(blank) is False, repr(blank)


def test_not_in_sources_when_retrieval_not_confident(monkeypatch, canned_retrieval):
    canned_retrieval(confident=False)
    monkeypatch.setattr(answer, "call_groq", _good_response)  # must not be called
    result = answer_question("What is the NAV of SBI Magnum?")
    assert result.mode == "not_in_sources"
    assert result.hits == []
    assert "HDFC" in result.text


def test_extractive_fallback_shape():
    fallback = extractive_fallback(HITS)
    assert fallback.mode == "extractive"
    assert fallback.text.startswith("From the source page:")
    assert fallback.source_url == HITS[0].metadata["source_url"]


def test_answer_rejects_pii_in_prompt_never_echoed(monkeypatch):
    # The generated answer must not echo a PAN even if the (fake) model is tempted.
    monkeypatch.setattr(
        answer,
        "call_groq",
        lambda *a, **k: (
            "Your PAN is ABCDE1234F and the expense ratio is 1.03%.\n"
            "Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
        ),
    )
    result = answer_question("What is the expense ratio?")
    # PII echo -> fallback keeps the source text, never the PAN.
    assert result.mode == "extractive"
    assert "ABCDE1234F" not in result.text


def test_requiring_key_is_part_of_failure_handling(monkeypatch, tmp_path):
    # call_groq with no key returns None (require_api_key raises inside), so the
    # orchestrator takes the degraded path instead of crashing.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert call_groq("question", "context") is None


# ---------------------------------------------------------------------------
# Memory window contract (FR-18)
# ---------------------------------------------------------------------------


def test_answer_question_forwards_a_trimmed_history_to_retrieval(monkeypatch):
    """`answer_question` re-trims to MEMORY_TURNS so no caller can exceed the bound, and
    the window it forwards is exactly what retrieval resolves against."""
    captured = {}

    def spy(question, **kwargs):
        captured["question"] = question
        captured["history"] = list(kwargs.get("history", ()))
        return RetrievalResult(
            hits=HITS,
            confident=True,
            newest_fetched_at=HITS[0].metadata["fetched_at"],
        )

    monkeypatch.setattr(answer, "retrieve", spy)
    monkeypatch.setattr(answer, "call_groq", _good_response)

    oversized = [f"turn {i}" for i in range(config.MEMORY_TURNS + 5)]
    answer_question("What is its exit load?", history=oversized)

    assert len(captured["history"]) == config.MEMORY_TURNS
    assert captured["history"] == oversized[-config.MEMORY_TURNS :]


def test_the_model_sees_a_self_contained_question_for_a_carried_referent(monkeypatch):
    """AQ-2: only the prompt question is rewritten, so the model is not left guessing what
    "its" referred to, while the caller's stored question stays untouched."""
    seen = {}

    def capture(question, *args, **kwargs):
        seen["prompt_question"] = question
        return _good_response()

    monkeypatch.setattr(
        answer, "retrieve",
        lambda q, **kw: RetrievalResult(
            hits=HITS, confident=True,
            newest_fetched_at=HITS[0].metadata["fetched_at"],
        ),
    )
    monkeypatch.setattr(answer, "call_groq", capture)

    history = ["Who manages the small cap fund?"]
    answer_question("What is its exit load?", history=history)
    assert config.SCHEME_DISPLAY_NAMES["small_cap"] in seen["prompt_question"]


def test_a_self_contained_question_reaches_the_model_unchanged(monkeypatch):
    seen = {}

    def capture(question, *args, **kwargs):
        seen["prompt_question"] = question
        return _good_response()

    monkeypatch.setattr(
        answer, "retrieve",
        lambda q, **kw: RetrievalResult(
            hits=HITS, confident=True,
            newest_fetched_at=HITS[0].metadata["fetched_at"],
        ),
    )
    monkeypatch.setattr(answer, "call_groq", capture)

    answer_question("What is the expense ratio?", history=["Who manages the small cap fund?"])
    assert seen["prompt_question"] == "What is the expense ratio?"


def test_memory_window_uses_user_turns_only_and_stays_bounded():
    """`_memory_window` is the single place the UI builds memory, so the two properties
    the rest of the design leans on are pinned here: assistant turns are never used as
    memory, and the window cannot grow past MEMORY_TURNS."""
    from app import _memory_window

    messages = [
        {"role": "user", "content": "Who manages the small cap fund?"},
        {"role": "assistant", "answer": HITS[0]},
        {"role": "user", "content": "What is its exit load?"},
    ]
    assert _memory_window(messages) == [
        "Who manages the small cap fund?",
        "What is its exit load?",
    ]

    long_session = [
        entry
        for i in range(config.MEMORY_TURNS + 8)
        for entry in ({"role": "user", "content": f"question {i}"},
                      {"role": "assistant", "answer": HITS[0]})
    ]
    window = _memory_window(long_session)
    assert len(window) == config.MEMORY_TURNS
    assert window[0] == f"question 8"
    assert window[-1] == f"question {config.MEMORY_TURNS + 7}"

    assert _memory_window([]) == []


def test_app_module_has_no_bare_strings_that_streamlit_would_render():
    """Module-level docstrings under an assignment are printed to the page by Streamlit.

    Streamlit evaluates a bare string expression that follows a module-level assignment and
    renders it as markdown, so a developer docstring on a constant appears to the user as a
    stray paragraph above the title. Three of them were showing above the heading before this
    was caught. A module docstring is unaffected, so it is allowed.
    """
    import ast
    import pathlib

    import app

    source = pathlib.Path(app.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    docstring = ast.get_docstring(tree, clean=False)
    offenders = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and node.value.value != docstring
    ]
    assert offenders == [], [text.value[:60] for text in offenders]


# ---------------------------------------------------------------------------
# Turn rendering
# ---------------------------------------------------------------------------


def _stub_answer(question, history=None):
    return Answer(
        text="The exit load is 1% if redeemed within one year.",
        source_url=HITS[0].metadata["source_url"],
        source_title=HITS[0].metadata["page_title"],
        last_updated="2026-09-28T20:18:28+05:30",
        hits=HITS,
        mode="generated",
    )


def _app_with_stubbed_answers(monkeypatch):
    """An AppTest on the real app.py, with the embedder and the model stubbed out.

    Keeps the test fast and, more to the point, needs no API key and no network, so the
    rendering order and the no-echo guarantee can be asserted on every run rather than only
    on a machine where retrieval happens to be quick.
    """
    import app
    from streamlit.testing.v1 import AppTest

    class _StubCollection:
        @staticmethod
        def count():
            return 5

    monkeypatch.setattr(app, "_resources", lambda: (None, _StubCollection()))
    monkeypatch.setattr(app, "answer_question", _stub_answer)
    return AppTest.from_file(app.__file__, default_timeout=60)


def test_the_question_is_drawn_before_the_answer_is_known(monkeypatch):
    # The turn is drawn in order, user message then assistant answer, so the question is on
    # screen while retrieval and generation are still running rather than after them.
    at = _app_with_stubbed_answers(monkeypatch)
    at.run()
    at.chat_input[0].set_value("What is the exit load on the large cap fund?").run()

    assert not at.exception
    roles = [message.name for message in at.chat_message]
    assert roles[-2:] == ["user", "assistant"], roles
    assert (
        "What is the exit load on the large cap fund?"
        in at.chat_message[-2].markdown[0].value
    )
    assert "exit load is 1%" in at.chat_message[-1].markdown[0].value


def test_blocked_input_is_never_echoed_into_the_transcript(monkeypatch):
    # C2, and the reason the guards run before anything is drawn: a PAN is refused without
    # being rendered back as a user message, and it never reaches session state.
    at = _app_with_stubbed_answers(monkeypatch)
    at.run()
    at.chat_input[0].set_value("My PAN is ABCDE1234F, what is the exit load?").run()

    assert not at.exception
    rendered = " ".join(
        element.value
        for group in (at.markdown, at.caption, at.info, at.warning, at.error, at.text)
        for element in group
    )
    assert "ABCDE1234F" not in rendered
    assert "user" not in [message.name for message in at.chat_message]
    assert at.session_state["messages"] == []


def _labels(at):
    return [button.label for button in at.button]


def test_a_starter_chip_is_honoured_even_though_its_own_click_hides_it(monkeypatch):
    # The chips are offered once and then removed, which is the regression to guard. A button
    # read with `if st.button(...)` needs to still be rendered later in the same run to report
    # True -- and the run that processes a chip is precisely the run that stops rendering them,
    # so the first chip would vanish the instant it was clicked and do nothing. The `on_click`
    # callback is what makes it survive.
    import app

    at = _app_with_stubbed_answers(monkeypatch)
    at.run()
    assert _labels(at) == list(app.EXAMPLE_QUESTIONS)

    at.button(key=f"example_{app.EXAMPLE_QUESTIONS[0][:24]}").click().run()

    assert not at.exception
    asked = [
        message["content"]
        for message in at.session_state["messages"]
        if message["role"] == "user"
    ]
    assert asked == [app.EXAMPLE_QUESTIONS[0]]


def test_the_starter_chips_are_offered_once_at_the_top(monkeypatch):
    # Offered on the opening screen only, and above the greeting. They must not reappear
    # above every later question, which is what "only once" means here.
    import app

    at = _app_with_stubbed_answers(monkeypatch)
    at.run()
    assert _labels(at) == list(app.EXAMPLE_QUESTIONS)
    assert at.session_state["opened"] is False

    at.chat_input[0].set_value("What is the exit load on the large cap fund?").run()
    assert not at.exception
    assert _labels(at) == ["New chat"]
    assert at.session_state["opened"] is True

    at.chat_input[0].set_value("And the lock-in period?").run()
    assert not at.exception
    assert _labels(at) == ["New chat"]


def test_a_refused_question_does_not_bring_the_chips_back(monkeypatch):
    # A refusal stores no turn, so `messages` alone cannot tell "never asked" from "asked and
    # refused". Without the durable `opened` flag the chips would reappear on the next
    # interaction, which is the bug this covers.
    import app

    at = _app_with_stubbed_answers(monkeypatch)
    at.run()
    at.chat_input[0].set_value("Should I buy the small cap fund?").run()

    assert not at.exception
    assert at.session_state["messages"] == []
    assert at.session_state["opened"] is True

    at.run()
    for label in _labels(at):
        assert label not in app.EXAMPLE_QUESTIONS, label
    assert _labels(at) == ["New chat"]


def test_a_blocked_question_leaves_the_opening_screen_alone(monkeypatch):
    # Nothing is drawn for a `block`, so the opening screen must not be consumed either --
    # otherwise a pasted PAN silently costs the user their starter chips.
    at = _app_with_stubbed_answers(monkeypatch)
    at.run()
    at.chat_input[0].set_value("My PAN is ABCDE1234F").run()

    assert not at.exception
    assert at.session_state["opened"] is False


def test_a_refused_question_is_echoed_so_the_refusal_reads_correctly(monkeypatch):
    # Every refusal must leave its question visible. Only `block` is silent, because that is
    # the PII path; a bare "outside the facts in my sources" with nothing above it reads as
    # though the app answered something else.
    at = _app_with_stubbed_answers(monkeypatch)
    at.run()
    at.chat_input[0].set_value("How do I download my capital gains statement?").run()

    assert not at.exception
    on_screen = " ".join(
        element.value for message in at.chat_message for element in message.markdown
    )
    assert "How do I download my capital gains statement?" in on_screen
    assert at.session_state["messages"] == []


def test_the_new_chat_control_is_there_from_the_first_answer_onward(monkeypatch):
    # `_ask` stores the turn after the header has already rendered, so keying the reset button
    # off `messages` hid it for exactly the run it is first wanted, and it only appeared on the
    # next interaction. It is keyed off `opening` instead.
    at = _app_with_stubbed_answers(monkeypatch)
    at.run()
    assert _labels(at) != ["New chat"]

    at.chat_input[0].set_value("What is the exit load on the large cap fund?").run()
    assert not at.exception
    assert _labels(at) == ["New chat"]


def test_new_chat_brings_the_starter_chips_back(monkeypatch):
    import app

    at = _app_with_stubbed_answers(monkeypatch)
    at.run()
    at.chat_input[0].set_value("What is the exit load on the large cap fund?").run()
    assert _labels(at) == ["New chat"]

    at.button(key="new_chat").click().run()

    assert not at.exception
    assert _labels(at) == list(app.EXAMPLE_QUESTIONS)
    assert at.session_state["messages"] == []
    assert at.session_state["opened"] is False
