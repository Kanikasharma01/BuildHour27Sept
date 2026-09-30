# PRD — Mutual Fund Facts-Only RAG Chatbot

**Project type:** Class milestone prototype (RAG demo)
**Status:** Draft v1.0
**Owner:** Team (Demo)
**Source brief:** `Docs/ProblemStatement.text`

---

## 1. Document Control

| Field | Value |
|---|---|
| Product name | MF FAQ Assistant (working title: *Fund Facts Bot*) |
| Version | 1.0 (prototype) |
| Last updated | 2026-09-28 |
| Audience | Instructor / milestone reviewers, demo attendees |
| Related artifacts | `README.md`, `CHUNKING.md`, `chunks.txt`, `SOURCES.md`, `sample_qa.md`, `.env.example` |

---

## 2. Problem Statement

Retail users and support/content teams repeatedly ask the same factual questions about mutual fund schemes — expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer, benchmark, and how to download statements. The answers live in official public pages (factsheets, KIM/SID, scheme FAQs, fee/charge pages, riskometer/benchmark notes, statement/tax-doc guides), but those pages are long, differ per scheme, and are tedious to search.

Generic chatbots are worse than no chatbot for this use case: they blend in stale or invented numbers, and they drift into investment advice — which is both unhelpful and inappropriate in a financial context.

**We need a small, demonstrable RAG chatbot that answers facts about a tightly scoped set of mutual fund schemes using only official public source pages, cites exactly one source link in every answer, and politely refuses advice-style questions.**

---

## 3. Goals

| # | Goal | How we measure it |
|---|---|---|
| G1 | Answer factual MF questions with correct, source-backed facts | ≥ 8 of 10 sample queries answer correctly with a working citation |
| G2 | Ground every answer in retrieved source text (true RAG, no free recall) | 0 answers in sample set contain a fact absent from the corpus |
| G3 | Every answer carries exactly one clear citation link | 10/10 sample answers contain a resolvable link from the approved source list |
| G4 | Refuse opinionated / portfolio questions politely | 3/3 adversarial questions refused with the facts-only message + educational link |
| G5 | No PII ever accepted, stored, or logged | PII filter blocks all 5 seeded test patterns; no PII in logs or `chroma_db/` |
| G6 | No performance/return claims | 2/2 performance questions deflected to the official factsheet, no numbers computed |
| G7 | Answers are short and timestamped | 10/10 answers ≤ 3 sentences and include `Last updated from sources:` |
| G8 | The RAG architecture is inspectable | `chunks.txt` human-readable; chunking rationale documented before code was written |

## 4. Non-Goals (Out of Scope)

- Transactional features: buy/sell, SIP registration, portfolio tracking, statements generation.
- User accounts, authentication, sessions, or multi-tenancy.
- Live NAV, live pricing, or any market data feed.
- Return/performance computation, ranking, or comparison of schemes.
- Regulatory-grade compliance, audit trails, or disclaimer archiving.
- Mobile-native app, user accounts, or production-scale ingestion.
- Broaden beyond the single AMC and 3–5 schemes defined in §6.
- Third-party blog/newsletter content as a source.

---

## 5. Users & Use Cases

**Primary — Retail investor comparing schemes**
> "What's the expense ratio of the HDFC Large Cap Fund Direct Growth?" → one-sentence answer + link to the fee/charges page. No suitability opinion.

**Primary — Support / content teammate**
> "What's the minimum SIP for the flexi-cap scheme and is there an exit load?" → answer from the scheme FAQ page + link, so they can paste it into a ticket.

**Secondary — Milestone reviewer / instructor**
> Asks an out-of-scope or advice question to probe the guardrails → assistant politely declines, states it is facts-only, and points to an SEBI/AMC educational link.

### Out-of-scope queries we explicitly expect (and must handle)

| Query type | Example | Expected behaviour |
|---|---|---|
| Factual, in scope | "ELSS lock-in period?" | Answer + 1 citation |
| Comparative fact | "Is exit load higher on the small-cap?" | Answer both values factually if in corpus, + 1 citation; no "which is better" |
| Performance | "Which of these gave the best 5-year return?" | Refuse to compute; link official factsheet |
| Advice | "Should I buy the ELSS?" | Refuse, facts-only message + educational link |
| Portfolio | "Is my portfolio too risky?" | Refuse, facts-only message + educational link |
| Out-of-corpus fact | "Expense ratio of a Parag Parag Flexi Cap?" | State scheme not in scope, list supported schemes |
| PII | "My PAN is ABCDE1234F, update my folio" | Block, do not echo, instruct to never share |

---

## 6. Scope — Corpus Definition

**AMC:** HDFC Asset Management (HDFC AMC)
**Category:** Large Cap, Flexi Cap, ELSS (Tax Saver), Small Cap, Balanced Advantage (Hybrid)
**Plans:** Direct Growth for all five
**Source count:** exactly 5 scheme URLs (mandatory) + a small set of official reference pages

### 6.1 Mandatory scheme URLs (from the brief)

| # | Category | Scheme | URL |
|---|---|---|---|
| 1 | Large Cap | HDFC Large Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| 2 | Flexi Cap | HDFC Equity (Flexi Cap) Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| 3 | ELSS | HDFC ELSS Tax Saver Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth ⚠️ |
| 4 | Small Cap | HDFC Small Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth |
| 5 | Balanced Advantage (Hybrid) | HDFC Balanced Advantage Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth |

### 6.2 Official reference pages (supporting, still "public sources only")

- HDFC AMC official scheme pages / factsheet PDFs (`hdfcamc.com`)
- AMFI scheme/NAV & benchmark reference (`amfiindia.com`)
- SEBI mutual fund investor education pages (`sebi.gov.in`) — used for refusal/education links

### 6.3 Source policy — ⚠️ open risk (see §18, OQ-1)

The brief names the five Groww URLs as the sources to collect, while also requiring pages "from AMC/SEBI/AMFI" and forbidding third-party blogs. Groww is a regulated distributor, not a blog, but it is not the AMC.

**Interim decision:** ingest the 5 specified URLs as the primary corpus (they are the URLs the brief enumerates), and cross-check every numeric fact (expense ratio, exit load, min SIP, lock-in) against the HDFC AMC factsheet or AMFI record before it enters a final answer. If a value cannot be corroborated on an official page, the assistant must say the value is unverified and link the official page instead. Document this in the README's "Scope" and "Known limits" sections.

---

## 7. Key Constraints & Guardrails

These are hard requirements from the brief and are treated as release-blocking.

| ID | Constraint | Enforcement point |
|---|---|---|
| C1 | **Public sources only.** No screenshots of the app backend; no third-party blogs as sources. | Allow-list of 5 scheme URLs + official reference domains. Ingestion rejects anything else. |
| C2 | **No PII.** Never accept or store PAN, Aadhaar, account numbers, OTPs, emails, phone numbers. | Pre-LLM input filter (§12.1) blocks and redacts before any model call or logging. |
| C3 | **No performance claims.** Do not compute or compare returns. | Prompt rule + keyword gate (§12.2); deflects to official factsheet link. |
| C4 | **No advice.** Refuse opinionated/portfolio questions. | Intent gate (§12.2) → polite refusal + educational link. |
| C5 | **≤ 3 sentences per answer.** | Prompt rule + post-generation validator that truncates/retries if violated. |
| C6 | **Exactly one citation link per answer.** | Answer contract (§11) + post-generation validator. |
| C7 | **`Last updated from sources:` stamp on every answer.** | Derived from the max `fetched_at` of the retrieved chunks; appended by code, not by the LLM. |
| C8 | **API keys in `.env`, never committed.** | `.env` gitignored; `.env.example` with blank placeholders committed. |

---

## 8. System Architecture

Two clearly separated stages, both mandatory per the brief.

### 8.1 Stage 1 — Ingestion (offline, runs once)

```
 5 scheme URLs + official reference pages
                │
                ▼
        [1] FETCH & EXTRACT        requests + BeautifulSoup/trafilatura
                │                  → main content only, strip nav/ads/footer
                ▼
        [2] NORMALIZE              collapse whitespace, keep headings + tables
                │                  → markdown-ish text
                ▼
        [3] CHUNK                  heading-aware / section-aware split  (§10)
                │                  → chunks.txt written for inspection
                ▼
        [4] EMBED                   sentence-transformers/all-MiniLM-L6-v2
                │                  → 384-dim vectors (one embedding per chunk)
                ▼
        [5] STORE                   ChromaDB, persistent on disk (./chroma_db)
                │                  → collection "mf_faqs", with metadata (§10.2)
                ▼
     chunks.txt + chroma_db/  ← ingestion is NOT repeated on restart
```

### 8.2 Stage 2 — Retrieval + Answer (online, per query)

```
 user question
        │
        ▼
 [6] INPUT GUARD               PII scan (C2) → hard block if hit
        │
        ▼
 [7] INTENT GATE               advice? / performance? / out-of-corpus? (C3, C4)
        │                       → polite refusal + educational link, END
        ▼
 [8] QUERY EMBED               same all-MiniLM-L6-v2 model → 384-dim
        │
        ▼
 [9] RETRIEVE                  Chroma similarity search, top-k (k≈5)
        │                       + score floor + optional MMR diversity
        ▼
[10] CONTEXT ASSEMBLY          top chunks + page titles + headings + fetched_at
        │
        ▼
[11] LLM GENERATE              Groq, temperature≈0, context-only prompt
        │                       answer ≤3 sentences + 1 citation
        ▼
[12] POST-VALIDATE             sentence count, citation presence, citation in
        │                       allow-list, no-PII-echo → repair or fallback
        ▼
[13] RESPONSE                  answer + citation + "Last updated from sources: <ts>"
```

---

## 9. Functional Requirements

Each requirement is demo-testable. **P** = must-have for the milestone.

### Stage 1 — Ingestion

| ID | P | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-1 | ✅ | Fetch all 5 mandatory scheme URLs + official reference pages, once, and cache raw text to `data/raw/*.txt` | All 5 schemes present in `data/raw/`; fetch failures logged and surfaced, not silently skipped |
| FR-2 | ✅ | Extract readable main content only | No nav/footer/cookie-banner/marketing text in `data/raw/`; each file ≥ 300 words |
| FR-3 | ✅ | **Write `chunks.txt` (human-readable) containing every chunk** | `chunks.txt` has all chunks with source URL, heading path, chunk index, and text, separated by `---` |
| FR-4 | ✅ | Document the chunking strategy in `CHUNKING.md` **before implementation**, with rationale, chunk size, overlap, and metadata kept | `CHUNKING.md` includes an "inspect the data first" observation section, the proposed numbers, and the reason they suit web-page FAQs |
| FR-5 | ✅ | Persist embeddings in ChromaDB on disk | `./chroma_db/` created; a second run of the app does **not** re-embed (ingestion is a separate command) |
| FR-6 | ✅ | Attach source metadata to every chunk (see §10.2) | Every Chroma record has all required metadata fields populated |
| FR-7 |  | Ingestion is idempotent and re-runnable via a single command | `python -m app.ingest` re-runs cleanly; a `--force` flag rebuilds |

### Stage 2 — Retrieval & Answer

| ID | P | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-8 | ✅ | Accept a free-text question and return a grounded answer | End-to-end query returns text + link + timestamp |
| FR-9 | ✅ | Exactly one citation link per factual answer, drawn from the ingested source list | Link is present, resolvable, and its domain is in the allow-list |
| FR-10 | ✅ | Answers are ≤ 3 sentences | Counted programmatically in the sample-QA test script |
| FR-11 | ✅ | Append `Last updated from sources: <timestamp>` to every answer | Timestamp = newest `fetched_at` among retrieved chunks; ISO-8601 or `YYYY-MM-DD HH:MM IST` |
| FR-12 | ✅ | Refuse advice/portfolio questions politely, with a facts-only message + educational link | Seeded advisory queries produce a refusal containing "facts-only" and an SEBI/AMC education link |
| FR-13 | ✅ | Refuse to compute or compare returns; link the official factsheet instead | Performance queries return no numbers, only a factsheet link |
| FR-14 | ✅ | Handle out-of-corpus schemes gracefully | Names the supported schemes instead of guessing |
| FR-15 | ✅ | Block PII before any model call or log write | PAN/Aadhaar/10-digit account/OTP/email/phone patterns all blocked; input never written to logs |
| FR-16 | ✅ | If retrieval confidence is below the score floor, say "I couldn't find that in the source pages" + list sources | Low-relevance test query does not produce a confident answer |
| FR-17 |  | Show retrieval provenance in the UI (source page + heading per answer) | Expandable "sources" section under each answer |
| FR-18 |  | Multi-turn context: follow-up questions can resolve pronouns ("its exit load?") | Follow-up test query resolves to the prior scheme |

### UI

| ID | P | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-19 | ✅ | Welcome line, 3 example questions, disclaimer note | First screen shows all three elements on load |
| FR-20 | ✅ | Disclaimer shown persistently: "Facts-only. No investment advice." | Visible in header and in the sidebar/about |
| FR-21 | ✅ | Show answer, one citation link, and `Last updated from sources:` | Present on every assistant message |
| FR-22 |  | Clickable example questions that prefill/submit the query | Three working buttons |

---

## 10. Data & Chunking Specification

> Per the brief, the strategy below was chosen **after inspecting the fetched data**. Final numbers are confirmed during implementation and recorded in `CHUNKING.md`.

### 10.1 Observation of the data (what drove the strategy)

- Source is **web pages, not prose documents**: facts are concentrated in labelled fields (Expense ratio, Exit load, Minimum SIP, Benchmark, Riskometer), short FAQ entries, and small tables.
- Content is **short and highly repetitive across the five schemes** (near-identical field labels, different values).
- Facts are **scattered across page sections**, so a fixed-size window would frequently split a field from its label.
- A **256-token hard cap** applies at the model level (all-MiniLM-L6-v2 max sequence length), so chunks must stay well under that or the tail of a chunk is silently truncated by the tokenizer.

### 10.2 Proposed strategy

| Parameter | Value | Why |
|---|---|---|
| Unit | **Section-aware, then sentence-packed** | Keeps "Expense ratio: 1.16%" and its label inside the same chunk |
| Chunk size | **~180–220 words (≈ 900–1,100 chars)** | Stays under the 256-token model cap with headroom; large enough to hold a complete labelled fact plus its surrounding context |
| Overlap | **~20% (≈ 40 words)**, applied at sentence boundaries only | Preserves context across a boundary without duplicating whole facts; no mid-sentence cuts |
| Never merge across | Different page, or different top-level section | Keeps one chunk = one coherent topic, which makes citations precise |
| Minimum chunk | 25 words — dropped | Avoids orphan fragments that pollute retrieval |
| No chunking needed for | Table rows | Emitted as `Field: Value` text lines, one fact per line, then packed into chunks — this is what makes fees/benchmarks retrievable |

### 10.3 Metadata stored per chunk (Chroma) and written to `chunks.txt`

| Field | Example | Purpose |
|---|---|---|
| `source_url` | `https://groww.in/mutual-funds/...` | The one citation link |
| `page_title` | `HDFC Large Cap Fund Direct Growth` | Display + citation label |
| `heading_path` | `Fees and charges > Expense ratio` | Fine-grained citation context |
| `scheme_name` | `HDFC Large Cap Fund – Direct Growth` | Scheme filtering / out-of-scope detection |
| `scheme_category` | `large_cap` | Out-of-scope detection, demos |
| `source_type` | `scheme_page \| factsheet \| faq \| charges \| education` | Citation quality; `education` links are for refusals only |
| `chunk_index` | `3` | Ordering / debuggability |
| `char_start`, `char_end` | `1180`, `2260` | Traceability back to the raw page |
| `content_hash` | `sha1:…` | Change detection on re-ingest |
| `fetched_at` | `2026-09-28T10:14:03+05:30` | Drives the `Last updated from sources:` stamp (C7) |

### 10.4 Retrieval config

| Parameter | Value | Why |
|---|---|---|
| `top_k` | 5 | Enough context without drowning the 3-sentence limit |
| Score floor | ≈ 0.25 cosine | Below this, answer "not in sources" (FR-16) instead of hallucinating |
| Diversity | Optional MMR, λ ≈ 0.7 | Avoids 5 near-duplicate chunks from the same page |
| Same model | `all-MiniLM-L6-v2` for chunks **and** queries | Required — mismatched embedding spaces silently break retrieval |

---

## 11. Answer Contract

Every factual answer must satisfy all of these, in this order:

```
<answer body — 1 to 3 sentences, factual only, no performance figures, no advice>

Source: <exactly one URL from the ingested source list>
Last updated from sources: <ISO-8601 timestamp of the newest retrieved chunk>
```

**Refusal shape:**

```
I'm a facts-only assistant — I don't give investment advice, recommendations,
or portfolio opinions. For a scheme's objective, riskometer, or fees, ask me about
the specific fact and I'll share the source page.

Learn more: <one SEBI / AMC investor-education link>
```

**Post-generation validator** (deterministic, not the LLM) rejects and repairs an answer if:
- more than 3 sentences,
- zero or more than one citation link,
- citation domain not in the allow-list,
- an echoing of PII the filter should have caught,
- a performance/return figure presented as a claim.

---

## 12. Safety & Refusal Behaviour

### 12.1 PII filter (pre-LLM, blocks the request)

Regex/keyword patterns for: PAN (`[A-Z]{5}[0-9]{4}[A-Z]`), Aadhaar (12 digits), account/folio numbers (8–18 digits), OTP (`\b\d{4,6}\b` with OTP context), email addresses, and phone numbers (+91 / 10-digit starting 6–9).

On hit: **do not call the LLM**, do not echo the matched value, do not write the input to any log, and return a "please don't share personal details" message.

### 12.2 Intent gate (pre-LLM, routes the query)

| Signal | Route | Output |
|---|---|---|
| PII pattern | Block | §12.1 message |
| Advice verbs (*should I, buy, sell, allocate, is it good, worth, suggest, which is better for me, my portfolio*) | Refuse | Facts-only refusal + education link (C4) |
| Performance terms (*return, CAGR, X-year return, best performing, alpha, ranking, vs benchmark*) | Deflect | No numbers; link official factsheet (C3) |
| Scheme not in the 5-scheme allow-list | Out of scope | Name the 5 supported schemes |
| Otherwise | Answer | §11 answer contract |

Both gates are rule-first (fast, deterministic, demo-visible) with the LLM used only for the grounded answer itself.

---

## 13. Non-Functional Requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | Cold query latency (retrieval + LLM, after model load) | < 5 s on demo laptop |
| NFR-2 | Startup time (app loads, models cached) | < 20 s; second run faster via model caching |
| NFR-3 | Runs fully locally except the Groq LLM call | Embeddings, vector DB, retrieval all on-device |
| NFR-4 | No API key in the repo | `.env` gitignored, verified pre-demo |
| NFR-5 | Works offline for retrieval | Kill network after ingest; retrieval still answers |
| NFR-6 | Deterministic answers | Temperature ≈ 0; sample-QA file reproducible |
| NFR-7 | Inspectable | `chunks.txt` + `CHUNKING.md` + `SOURCES.md` committed |
| NFR-8 | No PII at rest | Nothing user-typed is persisted; only the 5-URL ingest log |
| NFR-9 | Cheap to run | Entire demo under a few minutes; no long installs before the demo |
| NFR-10 | Honest failure | Network/API errors surface a clear message, never a fabricated answer |

---

## 14. Tech Stack (fixed by the brief)

| Layer | Choice | Version guidance |
|---|---|---|
| Language | Python | 3.10+ |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | Local, no API key, 384-dim. **Pinned** |
| Vector DB | ChromaDB, persistent client to `./chroma_db` | Ingestion runs once |
| LLM | Groq | Key in `.env` as `GROQ_API_KEY`, never committed |
| UI | Streamlit (tiny UI requirement) | `streamlit run` |
| Fetch/parse | `requests` + `beautifulsoup4` (+ `trafilatura` if needed) | Static text only; no headless browser |
| Orchestration | Plain Python modules (no agent framework) | Reviewers should be able to read the whole path |

Explicitly **not** used: OpenAI/Anthropic SDKs, LangChain/LlamaIndex (brief describes the pipeline explicitly, so it is hand-built for inspectability), any vector DB other than ChromaDB, any paid/hosted vector service.

---

## 15. Repository Layout

```
.
├── PRD.md                     ← this document
├── README.md                  ← setup, scope, known limits
├── CHUNKING.md                ← data inspection + chunking rationale (required)
├── SOURCES.md                 ← source list (5 URLs + official refs)
├── sample_qa.md               ← 5–10 queries, answers, links
├── DISCLAIMER.md              ← exact disclaimer text used in UI
├── .env.example               ← GROQ_API_KEY=  (blank)
├── .env                       ← real key, GITIGNORED
├── .gitignore
├── requirements.txt
├── app.py                     ← Streamlit UI
├── ingest.py                  ← Stage 1: fetch → clean → chunk → embed → store
├── retrieve.py                ← Stage 2: embed query → search → assemble context
├── answer.py                  ← prompt, Groq call, answer contract, validation
├── guards.py                  ← PII filter, intent gate, performance gate
├── config.py                  ← paths, model names, thresholds, allow-lists
├── data/
│   ├── raw/                   ← fetched page text
│   └── chunks.txt             ← human-readable chunks (FR-3)
├── tests/
│   ├── test_guards.py         ← PII + intent + performance gate tests
│   ├── test_contract.py       ← ≤3 sentences, exactly 1 citation
│   └── run_sample_qa.py       ← regenerates sample_qa.md
└── chroma_db/                 ← persisted vectors (gitignored, rebuildable)
```

---

## 16. Deliverables Checklist

| # | Deliverable (from brief) | Artifact | Done |
|---|---|---|---|
| D1 | Working prototype (app or notebook), or ≤3-min demo video | Streamlit app; backup screen recording | ☐ |
| D2 | Source list of the 5 URLs used (CSV or MD) | `SOURCES.md` | ☐ |
| D3 | README with setup steps, scope (AMC + schemes), known limits | `README.md` | ☐ |
| D4 | Sample Q&A file, 5–10 queries with answers + links | `sample_qa.md` | ☐ |
| D5 | Disclaimer snippet used in the UI | `DISCLAIMER.md` + literal string in `app.py` | ☐ |
| D6 | (Added) Chunking strategy doc + inspectable chunks | `CHUNKING.md`, `data/chunks.txt` | ☐ |

---

## 17. Milestones

| Phase | Work | Exit criteria |
|---|---|---|
| **M0 — Setup** | Repo, venv, `requirements.txt`, `.env.example`, `.gitignore` | App skeleton runs; `.env` ignored |
| **M1 — Inspect & propose chunking** | Fetch the 5 pages, read the data, write `CHUNKING.md` with rationale + numbers, generate `chunks.txt` | **Chunking doc written before embedding code.** Reviewer can read chunks |
| **M2 — Ingestion** | `ingest.py`: fetch → clean → chunk → embed (MiniLM) → persist to ChromaDB | `./chroma_db` populated; re-running the app does not re-ingest |
| **M3 — Retrieval** | `retrieve.py`: embed query with the same model, top-k search, score floor, context assembly | A known answer (e.g. "ELSS lock-in") retrieves the right chunk |
| **M4 — Generation + contract** | `answer.py`: context-only prompt, Groq, ≤3 sentences, 1 citation, timestamp | 10/10 contract checks pass |
| **M5 — Guards** | `guards.py`: PII filter, intent gate, performance gate; `tests/` | All PII patterns blocked; 3/3 advice queries refused |
| **M6 — UI** | Streamlit: welcome, 3 examples, disclaimer, answer + link + timestamp | Brief's "tiny UI" satisfied |
| **M7 — Docs & rehearsal** | `README.md`, `SOURCES.md`, `sample_qa.md`, `DISCLAIMER.md`, demo script, rehearse offline | All D1–D6 complete; demo runs without network for retrieval |

**Critical path:** M1 → M2 → M3 → M4. Everything else is small. If time is short, cut D1's polish, never C2 (PII) or C4 (no advice).

---

## 18. Risks & Open Questions

### Risks

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Groww values drift from official HDFC AMC data (expense ratio, exit load change) | High — an answer would be *wrong* while looking grounded | Cross-check every number against HDFC AMC factsheet/AMFI; state values with the source; put "values change — verify on the official factsheet" in the disclaimer and README limits |
| R2 | Source-policy ambiguity: brief lists Groww URLs but asks for AMC/SEBI/AMFI pages | Medium — reviewer may flag a non-AMC source | Resolve OQ-1 before ingest; state the decision in `README.md` |
| R3 | Web-page HTML changes or blocks automated fetches | Medium — ingestion fails at demo time | Cache raw text in `data/raw/`; commit `SOURCES.md`; keep a fallback copy of facts per scheme |
| R4 | MiniLM is a general-purpose embedder, weak on dense numeric fields | Medium — wrong chunk retrieved for "expense ratio" | Convert tables to `Field: Value` lines before chunking (§10.2); add numeric-field chunks; test the 6 core questions early |
| R5 | LLM drifts past 3 sentences or adds a second link | Medium — brief violation | Deterministic post-validator, not just prompt instructions |
| R6 | Groq API key missing/invalid/rate-limited at demo time | High — app looks broken | Fail loudly with a setup hint; rehearse with a cached answer set; record the ≤3-min video as backup (D1) |
| R7 | Scope creep into "helpful" behaviour (recommendations, comparisons) | Medium — violates C3/C4 | Keep the intent gate rule-first and blocking; treat any advice output as a bug |
| R8 | 256-token model cap silently truncates oversized chunks | Medium — facts lost | Enforce chunk size in code and assert it in tests |

### Open questions (resolve before/at M1)

- **OQ-1:** Is Groww acceptable as a primary source, given the AMC/SEBI/AMFI wording? *Recommended: yes for the corpus, with official cross-check and a stated README note.*
- **OQ-2:** Direct Growth only, or also Direct IDCW / Growth variants? *Recommended: Direct Growth only — matches the brief's 5 URLs and keeps the corpus small.*
- **OQ-3:** One LLM prompt path, or an LLM-based router for intent? *Recommended: rule-first gates; simpler to defend in a demo.*
- **OQ-4:** Grounding citation style — inline per-sentence or one link at the end? *Recommended: one link at the end (C6), with the heading path shown in the UI.*
- **OQ-5:** Which Groq model id? *Recommended: pin an explicit model in `config.py`; don't rely on an alias default.*

---

## 19. Acceptance Criteria (Demo Gate)

The prototype is demo-ready when **all** of the following are true:

1. `python -m app.ingest` populates `./chroma_db` and writes `data/chunks.txt`.
2. Starting the app a second time does **not** re-run ingestion.
3. "What is the exit load of the HDFC Small Cap Fund Direct Growth?" → correct value, ≤3 sentences, exactly 1 link, timestamp.
4. "What is the ELSS lock-in period?" → 3 years, ≤3 sentences, 1 link.
5. "Minimum SIP amount?" → amount, ≤3 sentences, 1 link.
6. "Should I buy the ELSS?" → refusal, facts-only wording, education link, no numbers.
7. "Which fund has the best 5-year return?" → no numbers; official factsheet link.
8. "My PAN is ABCDE1234F" → blocked, value not echoed, nothing logged.
9. "Expense ratio of the Parag Parag Flexi Cap?" → out-of-scope message listing the 5 supported schemes.
10. UI first screen shows: welcome line, 3 example questions, "Facts-only. No investment advice."
11. `README.md`, `SOURCES.md`, `sample_qa.md`, `DISCLAIMER.md`, `CHUNKING.md` all committed.
12. `.env` is gitignored and not committed.

---

## 20. Appendix

### A. Source URLs (authoritative list)

Scheme pages (mandatory, 5):
1. https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth
2. https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth
3. https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth ⚠️
4. https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth
5. https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth

Official reference domains (allow-listed): `hdfcamc.com`, `amfiindia.com`, `sebi.gov.in`.

> ⚠️ **Correction, Phase 2 (2026-09-28).** The ELSS URL as originally given in the
> brief — `.../hdfc-elss-tax-saver-fund-direct-growth` — returns **HTTP 404** and serves
> a Next.js `_notFoundPage`. The live URL uses `direct-plan-growth` instead, verified
> to return 200 with a populated scheme record. The other four URLs are unchanged and
> return 200. If the grader expects the brief's literal string, this substitution is
> the one documented deviation from the source list.

### B. Disclaimer snippet (verbatim, for UI + docs)

> **Facts-only. No investment advice.**
> This assistant answers factual questions about selected HDFC AMC mutual fund schemes using only the public source pages listed in `SOURCES.md`. It does not recommend, rate, or compare schemes, and it does not compute or report returns. Fund facts such as fees, exit loads, and minimum investments change over time — always verify on the official factsheet and consult your financial adviser before investing. Do not share PAN, Aadhaar, account numbers, OTPs, or any personal information here.

### C. The 3 example questions (UI)

1. "What is the expense ratio of the HDFC Large Cap Fund – Direct Growth?"
2. "What is the lock-in period for the HDFC ELSS Tax Saver Fund?"
3. "How do I download my capital gains statement?"

### D. Full sample-QA test set (5–10, per deliverable D4)

In scope: the 3 example questions above, plus:
4. "What is the minimum SIP amount for the HDFC Small Cap Fund – Direct Growth?"
5. "What is the exit load on the flexi cap scheme?"
6. "What is the benchmark and riskometer of the Balanced Advantage Fund?"
7. "Should I buy the ELSS tax saver fund?" *(refusal)*
8. "Which of these funds has the highest 5-year return?" *(deflect)*
9. "My folio number is 12345678 and my PAN is ABCDE1234F, please check my status." *(PII block)*

---

*End of PRD. Next artifact: `CHUNKING.md`, written after inspecting the fetched pages and before any embedding code.*
