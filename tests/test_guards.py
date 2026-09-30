"""Tests for the guard stage (PII filter + intent gate)."""

import re

import pytest

import config
import guards
from guards import classify_intent, pii_scan, run_guards


# ---------------------------------------------------------------------------
# PII
# ---------------------------------------------------------------------------


def test_all_pii_patterns_block():
    cases = {
        "pan": "My PAN is ABCDE1234F",
        "aadhaar_spaced": "My Aadhaar is 1234 5678 9012",
        "aadhaar_unspaced": "My Aadhaar is 123456789012",
        "account": "My folio number is 123456789012",  # 12 digits
        "account_8": "folio 12345678 please",  # 8 digits
        "otp": "the OTP is 8462",
        "email": "write to a.b@example.co.in",
        "phone": "call me on 9876543210",
        "phone_plus91": "+91-9876543210",
    }
    for name, question in cases.items():
        decision = run_guards(question)
        assert decision.action == "block", (name, question)


def test_pii_scan_names_patterns_not_values():
    match = pii_scan("My PAN is ABCDE1234F and my folio is 12345678")
    assert match.hit
    assert "pan" in match.patterns
    assert "account_folio" in match.patterns
    for pattern in match.patterns:
        assert pattern in {"pan", "aadhaar", "account_folio", "otp", "email", "phone"}


def test_pii_message_does_not_echo_input():
    question = "My PAN is ABCDE1234F and my folio is 12345678"
    decision = run_guards(question)
    assert decision.action == "block"
    assert decision.message is not None
    assert "ABCDE1234F" not in decision.message
    assert "12345678" not in decision.message


def test_pan_plus_advice_blocks_not_refuses():
    decision = run_guards("My PAN is ABCDE1234F, should I buy the ELSS fund?")
    assert decision.action == "block"


def test_clean_questions_not_pii():
    assert pii_scan("What is the exit load?").hit is False
    assert pii_scan("Minimum SIP investment amount?").hit is False
    assert pii_scan("Is there any lock-in period?").hit is False


# ---------------------------------------------------------------------------
# Supported schemes must never be refused
# ---------------------------------------------------------------------------


def test_each_supported_scheme_allowed():
    for name in config.SUPPORTED_SCHEMES.values():
        question = f"What is the expense ratio of {name}?"
        assert classify_intent(question) == "ALLOWED", name
        assert run_guards(question).action == "allow", name


# ---------------------------------------------------------------------------
# Intent gate
# ---------------------------------------------------------------------------


def test_advice_detected():
    assert classify_intent("Should I buy the ELSS tax saver fund?") == "ADVICE"
    decision = run_guards("Should I buy the ELSS tax saver fund?")
    assert decision.action == "refuse"
    assert "facts-only" in decision.message
    assert config.EDUCATION_URLS["sebi_mutual_funds"] in decision.message


def test_performance_detected():
    assert classify_intent("Which fund has the best 5-year return?") == "PERFORMANCE"
    decision = run_guards("Which fund has the best 5-year return?")
    assert decision.action == "deflect"
    assert config.FACTSHEET_URL in decision.message
    assert not re.search(r"\b\d\b", decision.message)  # no numbers at all


def test_out_of_corpus_detected():
    question = "Expense ratio of Parag Parag Flexi Cap?"
    assert classify_intent(question) == "OUT_OF_CORPUS"
    decision = run_guards(question)
    assert decision.action == "out_of_scope"
    for name in config.SUPPORTED_SCHEMES.values():
        assert name in decision.message


def test_out_of_corpus_non_hdfc_house():
    # PERFORMANCE beats OUT_OF_CORPUS by design (order is ADVICE, PERFORMANCE,
    # OUT_OF_CORPUS): a NAV/return query is deflected even for a foreign scheme.
    assert run_guards("What is the NAV of SBI Magnum? ").action == "deflect"
    assert run_guards("What is the expense ratio of SBI Magnum? ").action == "out_of_scope"
    assert run_guards("Is a gilt fund good for me? ").action == "out_of_scope"


def test_factual_question_allowed():
    question = "What is the expense ratio of HDFC Small Cap Fund - Direct Growth?"
    assert classify_intent(question) == "ALLOWED"
    assert run_guards(question).action == "allow"


# ---------------------------------------------------------------------------
# Uncovered fields (C8)
# ---------------------------------------------------------------------------


UNCOVERED_QUESTIONS = (
    "What is the AUM of the large cap fund?",
    "What are its assets under management?",
    "What are the net assets?",
    "What is the fund size?",
    "What is the corpus of the small cap fund?",
    "What are the top 10 holdings?",
    "What are its holdings?",
    "Is there a capital gains tax?",
    "What is the dividend yield?",
    "What is the volatility of the flexi cap fund?",
    "What is the Sharpe ratio?",
    "What is the Sortino ratio?",
    "What is the seat loss?",
    "Who is the custodian?",
    "Who is the trustee?",
)
"""Fields the five pages never state. These used to reach retrieval and clear the score
floor on unrelated text, so the app answered "What is the AUM?" with `confident=True` and
a citation to a `Charges and fees` chunk."""


@pytest.mark.parametrize("question", UNCOVERED_QUESTIONS)
def test_uncovered_field_is_deflected_rather_than_answered(question):
    decision = run_guards(question)
    assert decision.action == "deflect", question
    assert decision.message == guards.NOT_COVERED_DEFLECTION, question


COVERED_QUESTIONS = (
    "What is the expense ratio?",
    "What is the exit load?",
    "What is the portfolio turnover of the large cap fund?",
    "What is the scheme's objective?",
    "What is the category of the ELSS fund?",
    "Who is the registrar?",
    "What is the AMC?",
    "What is the capital appreciation objective?",
    "What is the minimum lump sum?",
    "Who manages it?",
)
"""Neighbouring phrasings that ARE answered by the corpus. Each sits close to a deflected
pattern, so this is the list that must not be broken: deflecting any of these would refuse
a question the sources can answer."""


@pytest.mark.parametrize("question", COVERED_QUESTIONS)
def test_covered_field_is_still_allowed(question):
    decision = run_guards(question)
    assert decision.action == "allow", (question, decision.message)


def test_uncovered_deflection_names_the_factsheet_and_no_number():
    message = guards.NOT_COVERED_DEFLECTION
    assert config.FACTSHEET_URL in message
    digits = re.findall(r"\b\d+\b", _NO_URL_RE.sub("", message))
    assert not digits, message


def test_wrong_house_wins_over_an_uncovered_field():
    """AUM plus a rival fund is still the wrong-house question; the field rule must not
    change which message the user gets."""
    decision = run_guards("What is the AUM of the SBI Magnum Midcap fund?")
    assert decision.action == "out_of_scope"


def test_performance_still_wins_over_an_uncovered_field():
    assert run_guards("What is the 1 year return on the large cap fund?").action == "deflect"
    assert (
        run_guards("What is the 1 year return on the large cap fund?").message
        == guards.PERFORMANCE_DEFLECTION
    )


def test_advice_still_wins_over_an_uncovered_field():
    assert (
        run_guards("Should I buy the large cap fund given its AUM?").action == "refuse"
    )


def test_pii_still_wins_over_an_uncovered_field():
    """Order is fixed: a PAN must be blocked, never deflected."""
    decision = run_guards("My PAN is ABCDE1234F, what is the AUM?")
    assert decision.action == "block"


# ---------------------------------------------------------------------------
# Message quality
# ---------------------------------------------------------------------------

_NO_URL_RE = re.compile(r"https?://\S+", re.I)


def _sentence_count(text: str) -> int:
    stripped = _NO_URL_RE.sub("", text).strip()
    if not stripped:
        return 0
    return len([s for s in re.split(r"(?<=[.!?])\s+", stripped) if s])


def test_all_refusal_messages_short_and_number_free():
    for message in (
        guards.PII_MESSAGE,
        guards.ADVICE_REFUSAL,
        guards.PERFORMANCE_DEFLECTION,
        guards.OUT_OF_SCOPE_MESSAGE,
        guards.NOT_COVERED_DEFLECTION,
    ):
        assert _sentence_count(message) <= 4, message
        digits = re.findall(r"\b\d+\b", _NO_URL_RE.sub("", message))
        assert not digits, message


def test_out_of_scope_lists_all_schemes():
    message = guards.OUT_OF_SCOPE_MESSAGE
    for name in config.SUPPORTED_SCHEMES.values():
        assert name in message