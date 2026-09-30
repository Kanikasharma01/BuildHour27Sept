"""Phase 8 Streamlit UI: a thin shell over the guard and answer stages.

    streamlit run app.py

Deliberately contains no embedding, search, or prompt logic (ARCHITECTURE.md 6). Every
turn is `guards.run_guards` first, then `answer.answer_question`; the UI decides only how
the returned `Answer` or `GuardDecision` looks on screen.

Three rules this file exists to enforce visibly in a demo:

- **Ingestion is never triggered here.** The embedder and the Chroma collection are
  opened read-only via `st.cache_resource`, and a missing or empty collection stops the
  app with "Run `python -m ingest` first" instead of silently building one (FR-5).
- **Rejected input is never stored.** A block/refuse/deflect/out_of_scope decision is
  rendered and then discarded; the question text is not appended to session state and is
  not logged, so a PAN pasted into the chat input is never echoed back (C2).
- **Refusals look different from answers.** They render in their own bordered container
  with a label, so a reviewer sees the guard fire rather than reading a refusal in the
  same typography as a grounded fact.
"""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

import config
import guards
from answer import answer_question
from ingest import load_embedder, open_collection

DISCLAIMER_PATH = Path(__file__).with_name("DISCLAIMER.md")

# The one line PRD FR-20 requires to be persistently visible. Rendered under the title on
# every rerun, so the fact stays on screen even mid-conversation.
#
# NOTE: written as a `#` comment, not a docstring, on purpose. Streamlit renders a bare
# string expression after a module-level assignment as page markdown, so a docstring here
# appears to the user as a stray paragraph above the title. Every constant below follows
# the same rule.
DISCLAIMER_HEADING = "**Facts-only. No investment advice.**"

# The short first-screen note required by FR-19 ("disclaimer note"). A summary of
# `DISCLAIMER.md`, not a replacement: the verbatim text stays one click away in the
# collapsed panel below, so the file's claim that the UI does not paraphrase it still holds.
#
# It must not point at UI that does not exist. This previously said the sources were "listed
# below", which referred to the sidebar source list, and then said every answer links its own
# source once that sidebar was gone. Both claims are now backed by the actual scope line
# rendered directly beneath it.
DISCLAIMER_NOTE = (
    "Answers come only from public HDFC scheme pages. Fees, exit loads and minimums change, "
    "so verify on the official factsheet before investing. Please don't share PAN, Aadhaar, "
    "account numbers or OTPs here."
)


def _scope_line() -> str:
    """The funds in scope, as links, generated from the corpus rather than written by hand.

    Built from `config.SOURCE_URLS` on every render so it cannot fall out of step with what
    was actually ingested. A hardcoded list of fund names is how a UI ends up promising a
    sixth scheme the retriever has never heard of, or silently dropping one it has.
    """
    links = ", ".join(
        f"[{spec.scheme_name}]({spec.url})" for spec in config.SOURCE_URLS
    )
    return f"The {len(config.SOURCE_URLS)} funds in scope: {links}."

# Shown under a spinner for the whole of the slow part of a turn, so the wait is visibly
# work rather than a frozen page. It names both stages because both are really happening:
# `_ask` cannot pause between them, the retriever and the model run inside one call.
THINKING_LABEL = "Searching the source pages and drafting an answer…"

# The three example questions, verbatim from PRD Appendix C (FR-19, FR-22).
#
# The third is the interesting one: the corpus covers scheme facts, not account servicing,
# so the fact is not in the sources. The model is expected to say so from the source blocks
# rather than invent a download flow. Retrieval currently still returns `confident=True`
# for it (0.36 on a Scheme identity chunk, above the 0.25 floor), so the answer arrives as
# a grounded "I could not find that in the source pages" rather than the `not_in_sources`
# path. That is the honest answer, but it relies on the model declining rather than on the
# retrieval stage recognising that the topic is absent from the corpus. The question is kept
# exactly as the PRD wrote it rather than swapped for an easier one, because a demo that only
# ever shows successes cannot show that the boundary works.
EXAMPLE_QUESTIONS = (    "What is the expense ratio of the HDFC Large Cap Fund – Direct Growth?",
    "What is the lock-in period for the HDFC ELSS Tax Saver Fund?",
    "How do I download my capital gains statement?",
)

_REFUSAL_LABELS = {
    "block": "Blocked — personal information detected",
    "refuse": "Not answered — investment advice",
    "deflect": "Not answered — outside the facts in my sources",
    "out_of_scope": "Not answered — scheme outside the corpus",
}

_SOURCE_LINE_RE = re.compile(r"^\s*Source:\s*\S+\s*$", re.M)
_UPDATED_LINE_RE = re.compile(r"^\s*Last updated from sources:.*$", re.M)

# Presentation only. Scoped to Streamlit's own test ids so it cannot restyle a refusal
# container into something that reads as a normal answer, and written as a `#` comment for
# the reason given above DISCLAIMER_HEADING: a bare string after a module-level assignment
# is rendered onto the page as markdown.
#
# The stylesheet deliberately does NOT touch the chat composer. An earlier version gave
# `stChatInput` a 999px radius and stripped the textarea's border and box-shadow, which
# fought Streamlit's own input styling and left the field's parts visibly overlapping. The
# composer's internals are laid out by Streamlit and are not ours to restyle; only the
# page-level frame is. Same reason the message bubbles are left alone.
_CSS = """
<style>
/* One narrow centred column, the way a messaging app reads.
   Only max-width is set. Padding is left entirely to Streamlit: it reserves bottom space
   for the pinned composer, and overriding it is how a last message ends up sitting behind
   the input. */
.block-container {
  max-width: 46rem;
}

/* A little air between turns without turning the page into a stack of cards. */
[data-testid="stChatMessage"] {
  gap: 0.7rem;
  padding: 0.3rem 0;
}

/* Starter chips: full-width rounded rows, left aligned, so they read as suggestions.
   A stadium radius was wrong here -- the example questions are long sentences, and a
   999px curve on a full-width button squeezes the text into the ends. */
[data-testid="stButton"] button {
  border-radius: 0.6rem;
  text-align: left;
  white-space: normal;
  height: auto;
  padding-top: 0.5rem;
  padding-bottom: 0.5rem;
}
[data-testid="stButton"] button:hover {
  border-color: #94a3b8;
}

/* Quieten the page chrome so the transcript is the page. */
#MainMenu, footer { visibility: hidden; }

h1 { font-size: 1.5rem; letter-spacing: -0.01em; }
</style>
"""


@st.cache_resource(show_spinner="Loading the embedder and the source collection...")
def _resources():
    """The embedder and the collection, loaded once per server process.

    `load_embedder` never re-embeds the corpus: it loads model weights only. The
    collection is opened, not built. Ingestion stays a separate command so that starting
    the app can never mutate `chroma_db/`.
    """
    return load_embedder(), open_collection()


def _read_disclaimer() -> str:
    """The disclaimer body from DISCLAIMER.md, minus its heading and its placement note."""
    if not DISCLAIMER_PATH.exists():
        return (
            "Facts-only. No investment advice. This assistant answers factual questions "
            "about five HDFC AMC schemes from public source pages only."
        )
    text = DISCLAIMER_PATH.read_text(encoding="utf-8")
    body = text.split("## Where this appears in the UI", 1)[0]
    return " ".join(
        line
        for line in body.splitlines()
        if line.strip()
        and not line.startswith("#")
        and line.strip() != DISCLAIMER_HEADING
    ).strip()


def _body_only(answer_text: str) -> str:
    """The answer without the citation and timestamp lines, which the UI renders itself."""
    stripped = _UPDATED_LINE_RE.sub("", answer_text)
    stripped = _SOURCE_LINE_RE.sub("", stripped)
    return stripped.strip()


def _render_sources(hits) -> None:
    """Expandable retrieval provenance: page, section, and score per chunk (FR-17)."""
    with st.expander(f"Sources ({len(hits)})"):
        for i, hit in enumerate(hits, 1):
            meta = hit.metadata
            st.markdown(
                f"**{i}. {meta['page_title']}** — {meta['heading_path']}  \n"
                f"similarity `{hit.similarity:.3f}` · {meta['scheme_category']}  \n"
                f"<{meta['source_url']}>",
                unsafe_allow_html=False,
            )


def _render_answer(answer) -> None:
    """A grounded answer, with its single citation and the code-appended timestamp."""
    if answer.notice:
        st.warning(answer.notice, icon="⚠️")

    if answer.source_url and answer.source_title:
        st.markdown(_body_only(answer.text))
        st.markdown(f"[{answer.source_title}]({answer.source_url})")
        st.caption(f"Last updated from sources: {answer.last_updated}")
    else:
        st.markdown(answer.text)

    if answer.hits:
        _render_sources(answer.hits)


def _render_refusal(decision) -> None:
    """A guard outcome, visibly distinct from a normal answer and with no echo of input."""
    with st.container(border=True):
        st.markdown(f"**{_REFUSAL_LABELS.get(decision.action, 'Not answered')}**")
        st.markdown(decision.message or "")
    st.caption("The guard stage stopped this before retrieval or the language model.")


def _ask(question: str) -> None:
    """Run one turn: guards first, then draw the turn, then answer it.

    The order here is the whole point, and it is load-bearing for two things at once.

    **Guards run before anything is drawn.** Nothing reaches the transcript until it has
    cleared `run_guards`, so a PAN pasted into the composer is refused without ever being
    rendered back to the screen (C2). `run_guards` is regex-only, so this costs nothing
    perceptible -- which is what makes it safe to delay the user's own message by a fraction
    of a millisecond in order to keep that guarantee. `block` is the only refusal that stays
    silent; the rest echo the question, because a refusal with no question above it is
    unreadable.

    **The message is drawn before the slow work.** Retrieval plus generation takes seconds.
    Previously the user saw nothing at all until both had finished, which reads as a hung
    page rather than a working one. The question is rendered first, then a spinner, so the
    turn is visibly in progress while `answer_question` blocks.

    The memory window is read strictly *before* the current question is appended, so the
    history handed to retrieval is earlier allowed questions only. Blocked PII is not in the
    transcript at all, so it cannot be carried.
    """
    decision = guards.run_guards(question)
    if decision.action != "allow":
        # Echo the question for every refusal except `block`. Advice, out-of-scope and
        # uncovered-fact refusals are not secret, and showing what was actually asked is what
        # makes the refusal legible -- a bare "outside the facts in my sources" with no
        # question above it reads as though the app answered something else. `block` is the
        # exception: the reason it fired is that the input contains a PAN, an OTP or similar,
        # so rendering it would put the very thing C2 forbids back on the screen.
        if decision.action != "block":
            with st.chat_message("user"):
                st.markdown(question)
            st.session_state.opened = True
        with st.chat_message("assistant"):
            _render_refusal(decision)
        return

    with st.chat_message("user"):
        st.markdown(question)
    st.session_state.opened = True

    with st.chat_message("assistant"):
        thinking = st.empty()
        with thinking.container():
            with st.spinner(THINKING_LABEL):
                history = _memory_window(st.session_state.messages)
                answer = answer_question(question, history=history)
        thinking.empty()
        st.session_state.messages.append({"role": "user", "content": question})
        st.session_state.messages.append({"role": "assistant", "answer": answer})
        _render_answer(answer)


def _memory_window(messages: list[dict]) -> list[str]:
    """The last `MEMORY_TURNS` user questions, oldest first (FR-18).

    Reads only `role == "user"` entries, so assistant text and the stored `Answer` objects
    are never used as memory. Sliced from the transcript on every turn rather than
    accumulated, so a long session cannot grow the window past the bound. Pure so the
    bound and the filtering are testable without a Streamlit runtime.
    """
    return [
        message["content"]
        for message in messages
        if message.get("role") == "user"
    ][-config.MEMORY_TURNS :]


def _render_history() -> None:
    """Replay the stored turns, so the transcript survives a rerun."""
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.markdown(message["content"])
            else:
                _render_answer(message["answer"])


def _inject_css() -> None:
    """Apply the chat-layout stylesheet.

    Layout and chrome only. Nothing here changes what is rendered, what is stored, or what
    a guard decided, so the CSS cannot make the app disagree with `answer.py`. The
    `st.chat_message` bubbles are left alone on purpose -- Streamlit already draws the user
    side as a bubble opposite the avatar, and overriding it is how a chat UI ends up looking
    like a stack of identical grey boxes.
    """
    st.markdown(_CSS, unsafe_allow_html=True)


def _render_api_key_notice() -> None:
    """Warn that answers will be quoted source text, only when that is actually true.

    This used to live in the sidebar, where it was invisible by default. Losing the sidebar
    would have removed the degraded-mode warning entirely, so it stays on the main screen --
    but it renders nothing when `GROQ_API_KEY` is set, so a working setup shows no banner.
    """
    if not config.api_key_present():
        st.warning(
            "GROQ_API_KEY is not set. Retrieval still works; answers fall back to quoted "
            "source text.",
            icon="⚠️",
        )


def _clear_transcript() -> None:
    """Empty the transcript so the greeting and starter chips come back.

    A callback rather than an `if` after the button, so the reset happens before the rerun
    that the click already triggers. `opened` goes back to False for the same reason, or the
    opening screen could never be shown twice in one session. `pending` is left alone
    deliberately: it is the in-flight question for the turn being rendered, and clearing it
    here would swallow a submission.
    """
    st.session_state.messages = []
    st.session_state.opened = False


def _header(opening: bool, disclaimer: str) -> None:
    """The title, the persistent FR-20 line, a reset control, and the full disclaimer.

    The disclaimer panel lives here rather than in the greeting so it stays available for
    the whole conversation: FR-20 asks for the disclaimer to be persistently visible, and
    putting it inside the first-screen greeting made it vanish the moment a question was
    asked. It is open on the opening screen and collapsed afterwards, so it costs one row.
    """
    left, right = st.columns([5, 1])
    with left:
        st.title("HDFC Mutual Funds")
        st.markdown(DISCLAIMER_HEADING)
    with right:
        # Spacer so the button sits on the title's baseline rather than above the fold.
        st.write("")
        # Keyed off `opening`, not off `st.session_state.messages`. The header renders before
        # `_ask` stores the turn, so on the run that produces the first answer the transcript
        # is still empty and a `messages` test would hide the button until the *next*
        # interaction -- the control would be missing for exactly the run it is first wanted.
        if not opening:
            st.button(
                "New chat",
                key="new_chat",
                width="stretch",
                on_click=_clear_transcript,
                help="Clear the transcript and start over",
            )
    with st.expander("Full disclaimer", expanded=opening):
        st.caption(disclaimer)


def _greeting() -> None:
    """The opening assistant message: what this is, and what it can see.

    Rendered as a real assistant turn rather than as loose page text, so the first thing on
    screen has the same shape as everything that follows it. Shown only while the transcript
    is empty.
    """
    with st.chat_message("assistant"):
        st.markdown(
            "Ask me a factual question about the five HDFC funds below. I answer from "
            "public scheme pages only, and I will tell you when something is not in them."
        )
        st.caption(DISCLAIMER_NOTE)
        st.caption(_scope_line())


def _set_pending(question: str) -> None:
    """Queue a question for this run, from a callback so the state is set before the script.

    Used as `on_click` on the starter chips. Streamlit runs callbacks as a prefix to the
    rerun, ahead of the script body, which is the whole reason a chip can honour the click
    that unmounts it: reading the click with a plain `if st.button(...)` would need the
    button to still be rendered later in the same run, and the run that processes a chip is
    exactly the run that stops rendering them.
    """
    st.session_state.pending = question


def _example_chips() -> None:
    """The PRD example questions as clickable starter rows (FR-19, FR-22).

    Shown once, above the greeting, and never again. See `main` for why the chip click is
    delivered by callback rather than by a return value.
    """
    st.caption("Try one of these:")
    for example in EXAMPLE_QUESTIONS:
        st.button(
            example,
            key=f"example_{example[:24]}",
            width="stretch",
            on_click=_set_pending,
            args=(example,),
        )


def main() -> None:
    st.set_page_config(
        page_title="HDFC Mutual Funds — facts-only Q&A",
        page_icon="📊",
        layout="centered",
    )

    try:
        _resources()
    except Exception:
        st.error(
            f"The source collection at `{config.CHROMA_PATH}` could not be opened. "
            "Run `python -m ingest` first, then reload this page."
        )
        st.stop()

    _embedder, collection = _resources()
    if collection.count() == 0:
        st.error(
            f"The collection `{config.COLLECTION}` is empty. Run `python -m ingest` "
            "first, then reload this page."
        )
        st.stop()

    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("pending", "")
    st.session_state.setdefault("opened", False)

    _inject_css()
    disclaimer = _read_disclaimer()

    # The composer is read first, before anything is decided about the opening screen. A
    # question typed into it is already known at this point, so the run that answers it does
    # not also draw the starter chips and the greeting above that answer. The input is still
    # pinned to the bottom of the page -- Streamlit positions the composer in CSS, not in
    # script order.
    question = st.chat_input("Ask about fees, exit loads, minimum investment, or risk")

    # True only while nothing has been asked yet. `opened` is what makes "offered once"
    # durable: a refusal stores no turn, so `messages` alone cannot distinguish "never asked"
    # from "asked and refused", and the chips would come back on the next interaction. `pending`
    # and `question` cover the run that is about to answer, so that run does not draw the
    # opening screen above the answer it is producing.
    opening = not (
        st.session_state.get("opened")
        or st.session_state.messages
        or st.session_state.pending
        or question
    )

    _header(opening, disclaimer)
    _render_api_key_notice()

    if opening:
        _example_chips()
        _greeting()

    _render_history()

    if question:
        st.session_state.pending = question

    if st.session_state.pending:
        _ask(st.session_state.pending)
        st.session_state.pending = ""


if __name__ == "__main__":
    main()
