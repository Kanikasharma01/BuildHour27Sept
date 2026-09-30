# IMPLEMENTATION.md — Phase-Wise Build Guide

**Companion to:** `ARCHITECTURE.md` v1.0 and `PRD.md` v1.0
**Purpose:** A copy-paste, phase-by-phase spec you can hand to Cursor (or any coding agent) so the RAG chatbot is built in the correct order, with a verification gate between every phase.
**Status:** Draft v1.0
**Last updated:** 2026-09-28

---

## 1. How to Use This Document

Work **one phase at a time**. For each phase:

1. Open Cursor at the repo root with `PRD.md`, `ARCHITECTURE.md`, and `IMPLEMENTATION.md` in context.
2. Copy the **Cursor Prompt** block from that phase verbatim.
3. Let Cursor implement. Do not let it run ahead into the next phase.
4. Run the **Manual Verification** commands yourself. Do not trust the agent's own report.
5. If the **Exit Gate** fails, paste the phase's *Repair Prompt* (or the error text) back into Cursor.
6. Only when the gate passes, move to the next phase and tell Cursor: *"Phase N is signed off. Proceed to Phase N+1."*

**Rules that keep this working:**

- Never skip Phase 3. The chunking decision is a brief requirement and it must be made from inspected data, not guessed.
- Never let Cursor implement a later phase "while it's convenient" — later phases depend on earlier module contracts being stable.
- If Cursor invents a threshold, a model id, or a URL, that is a defect. Thresholds live in `config.py`; model ids must be verified; URLs come from `PRD.md` Appendix A.
- Commit after every passing phase. Tag the milestone.

**Suggested Cursor context setup (once, at repo root):** add these to `.cursor/rules/rag-demo.mdc` so they are always in context:

```mdc
---
description: Ground rules for the MF facts-only RAG chatbot milestone
alwaysApply: true
---

Project: a RAG chatbot for a class demo. Facts-only about 5 HDFC AMC schemes.

Authoritative docs, read before changing anything:
- PRD.md            - requirements, constraints (C1-C8), acceptance criteria
- ARCHITECTURE.md   - component contracts, pipeline stages, ADRs
- IMPLEMENTATION.md - the current phase

Hard rules:
- Embeddings: sentence-transformers/all-MiniLM-L6-v2 (384-dim). Same model for chunks and queries.
- Vector DB: ChromaDB, persistent to ./chroma_db. Ingestion is a separate command, never on app startup.
- LLM: Groq. Key in .env only, never hardcoded, never logged, never committed.
- max_seq_length of the embedder is 256 tokens. Chunks must be asserted under that limit.
- PII guard runs BEFORE embedding and BEFORE any LLM call and BEFORE any logging.
- Answers: <= 3 sentences, exactly 1 citation link, "Last updated from sources:" appended by code.
- No LangChain / LlamaIndex / OpenAI SDK. Hand-built pipeline.
- No performance/return claims. No investment advice. Refuse those.
- Do not add features beyond the current phase. Do not refactor earlier phases without being asked.
- Match existing style. No inline comments; docstrings only where behaviour is non-obvious.
```

---

## 2. Conventions for All Phases

| Convention | Rule |
|---|---|
| Python | 3.10+, type hints on public functions, no `Any` unless unavoidable |
| Module layout | Flat modules at repo root: `config.py`, `ingest.py`, `retrieve.py`, `answer.py`, `guards.py`, `app.py` |
| Imports | Only in the direction given by ARCHITECTURE.md §3. `ingest.py` must never import `answer.py` or `guards.py` |
| Config | No magic numbers in logic. Every threshold, path, model id, and URL lives in `config.py` |
| Errors | Fail loudly with an actionable message. Never swallow an exception into a plausible-looking answer |
| Logging | `logging` module. Decision codes and hashed question prefixes only. Never raw user input, never the API key |
| Chunk IDs | Deterministic: `f"{scheme_slug}::{chunk_index:03d}"` |
| Chroma metadata | `str | int | float | bool` only. No lists, no `None`, no nesting. Coerce `None` → `""` |
| Comments | Sparing. Docstrings where behaviour is non-obvious |
| Tests | pytest, in `tests/`. Guards and contract tests first — they encode the brief's hard constraints |

---

## 3. Phase Map

| Phase | Deliverable | Gate type | Est. | Critical path |
|---|---|---|---|---|
| 0 | Repo scaffold, venv, deps | runs | 20 min | |
| 1 | `config.py`, secrets hygiene | runs | 20 min | |
| 2 | Fetch + extract + normalize → `data/raw/` | inspectable | 45 min | ● |
| 3 | Chunking + `CHUNKING.md` + `chunks.txt` | **human review** | 60 min | ● |
| 4 | Embed + persist → `chroma_db/` | retrieval sanity | 45 min | ● |
| 5 | `retrieve.py` (embed, search, floor, MMR) | retrieval sanity | 60 min | ● |
| 6 | `guards.py` (PII, intent, performance) | tests | 60 min | |
| 7 | `answer.py` (prompt, Groq, contract, validation) | tests | 75 min | ● |
| 8 | `app.py` Streamlit UI | manual | 60 min | |
| 9 | Test suite + `run_sample_qa.py` | tests | 60 min | |
| 10 | `README`, `SOURCES`, `sample_qa`, `DISCLAIMER` | review | 45 min | |
| 11 | Rehearsal + demo video backup | review | 45 min | |

**Total ≈ 9 hours of focused work.** Critical path (●) is Phases 2–5 and 7; those cannot be compressed. If time is short, cut UI polish (Phase 8) and the demo video (Phase 11) — **never** cut Phase 3, 6, or 7, which carry the brief's hard constraints.

---

## 4. Phase 0 — Repo Scaffold

**Goal:** A clean repo with a working venv and a hello-world import, so every later failure is ours, not the environment's.

**Prereqs:** none. Do this first, even if the folder already has files.

**Files to create:** `.gitignore`, `requirements.txt`, `README.md` (stub — full README is Phase 10), `.env.example`.

### Cursor Prompt — Phase 0

```md
Read PRD.md and ARCHITECTURE.md in full before writing anything.

Task: Phase 0 - repository scaffold for a Python RAG chatbot demo. Create ONLY these
files. Do not implement any RAG logic yet.

1. .gitignore must ignore: .env, .venv/, venv/, __pycache__/, *.pyc, chroma_db/,
   .streamlit/, .pytest_cache/. It must NOT ignore data/raw/ or data/chunks.txt
   (those are demo artifacts we want committed). Add a comment on the .env line
   explaining the key must never be committed.

2. requirements.txt with pinned minor versions for: streamlit, chromadb, sentence-transformers,
   groq, requests, beautifulsoup4, trafilatura, python-dotenv, pytest.
   Use these exact package names. Add a one-line comment at the top stating the
   embedding model is pinned in config.py, not here.

3. .env.example containing exactly two blank-valued lines:
   GROQ_API_KEY=
   GROQ_MODEL=
   with a comment saying to copy to .env and fill in, and to verify GROQ_MODEL
   against the current Groq model list.

4. README.md stub with the headings: Overview, Setup, Scope, Running, Known Limits -
   each with a one-line "TODO - filled in Phase 10" placeholder.

5. A single empty package marker is NOT needed; modules stay flat at repo root.

After writing the files, run `pip --version` to confirm the interpreter exists and
report which Python version is active. Do not install anything yet - I will run
the install myself.
```

### Manual Verification

```powershell
git --version
python --version
```

Then (in a venv — see README later, or just install directly if this is a throwaway demo env):

```powershell
pip install -r requirements.txt
python -c "import streamlit, chromadb, sentence_transformers, groq, requests, bs4, trafilatura, dotenv; print('deps ok')"
```

### Exit Gate

- [ ] `.gitignore` ignores `.env`
- [ ] `python -c "import chromadb"` succeeds
- [ ] `git status` shows `.env.example` tracked and `.env` ignored (create a throwaway `.env` to confirm)
- [ ] No RAG code exists yet

**Commit:** `chore: phase 0 scaffold`

---

## 5. Phase 1 — Configuration & Secrets

**Goal:** One place for every path, threshold, model id, and allow-list entry. This is the file every other module reads, so it must be right before anything depends on it.

**Files to create:** `config.py`.

### Cursor Prompt — Phase 1

```md
Read PRD.md sections 6, 7, 10, 13 and ARCHITECTURE.md section 7 first.

Task: Phase 1 - create config.py only. This is the single source of truth for paths,
model ids, thresholds, and the source allow-list. No other module exists yet.

Requirements:
- Load env vars with python-dotenv at import time. GROQ_API_KEY and GROQ_MODEL come
  from the environment only. Never hardcode a key. Never print the key.
- Provide a helper `require_api_key()` that raises a clear, actionable error naming
  the .env file if the key is missing. Do not raise at import time - the app must be
  able to start in the no-API-key degraded mode (ARCHITECTURE.md 5.6).
- Constants exactly as specified in ARCHITECTURE.md section 7: EMBED_MODEL, EMBED_DIM=384,
  CHROMA_PATH, COLLECTION (with hnsw:space cosine), RAW_DIR, CHUNKS_TXT,
  CHUNK_TARGET_WORDS=200, CHUNK_MAX_WORDS=220, CHUNK_OVERLAP_PCT=0.20, CHUNK_MIN_WORDS=25,
  TOP_K=5, SCORE_FLOOR=0.25, MMR_LAMBDA=0.7.
- Add MAX_EMBED_TOKENS = 256 with a comment-free docstring explaining that the embedder's
  max_seq_length truncates silently, so chunks must be asserted under it.
- SOURCE_URLS: the 5 scheme URLs listed in PRD.md Appendix A, as an ordered list, each
  paired with its scheme_name, scheme_category (large_cap, flexi_cap, elss, small_cap,
  balanced_advantage), and a scheme_slug used for chunk ids. Read them from the PRD -
  do not invent or shorten URLs.
- ALLOWED_DOMAINS: groww.in, hdfcamc.com, amfiindia.com, sebi.gov.in.
- EDUCATION_URLS: at least one sebi.gov.in investor-education page and one amfiindia.com
  page, used only for refusal replies. Mark clearly that these are for refusals only.
- SUPPORTED_SCHEMES: a mapping of lowercase scheme name -> display name for the 5 schemes,
  used for out-of-scope detection and the "I only cover these" message.
- `def url_allowed(url: str) -> bool` - domain check against ALLOWED_DOMAINS, used by both
  the ingest allow-list and the citation validator.
- `def allowed_model_ids() -> list[str]` - small helper for test_chunks.py to re-tokenize
  chunks with the real tokenizer.

Use type hints. No logic beyond the helpers. Print a summary when run as __main__ listing
scheme count, collection name, and whether the API key is present (present/absent only,
never the value).
```

### Manual Verification

```powershell
python config.py
python -c "from config import url_allowed; print(url_allowed('https://groww.in/mutual-funds/x'), url_allowed('https://random-blog.example/x'))"
```

Expect: a summary with 5 schemes, `True False`, and no key value printed.

### Exit Gate

- [ ] 5 scheme URLs, byte-identical to PRD Appendix A
- [ ] `url_allowed` returns `False` for any non-allow-listed domain
- [ ] Importing `config` without an API key does **not** raise
- [ ] `grep -i "sk-\|gsk_" config.py` finds nothing

**Commit:** `feat: phase 1 config`

---

## 6. Phase 2 — Fetch, Extract, Normalize

**Goal:** Get the 5 pages into readable local text and **actually read them**. This is the input to the chunking decision in Phase 3.

**Files to create:** `ingest.py` (fetch/extract/normalize only for now), `data/raw/`.

### Cursor Prompt — Phase 2

```md
Read ARCHITECTURE.md section 4 first. This is Stage 1 of the RAG pipeline.

Task: Phase 2 - implement the first three steps of ingest.py:
FETCH -> EXTRACT -> NORMALIZE. Do NOT implement chunking, embedding, or Chroma yet.

Implement a FetchedPage dataclass (url, title, scheme_name, scheme_category,
source_type, fetched_at ISO-8601 with +05:30, text, n_words).

- fetch_page(url, session): GET with a realistic User-Agent, a 30s timeout, and
  up to 3 retries with backoff. Raise a clear error on final failure.
- extract_main_text(html) -> str: prefer trafilatura for main-content extraction;
  fall back to BeautifulSoup. Remove script, style, nav, header, footer, aside, and
  noscript. Never use a headless browser.
- normalize_text(text) -> str: collapse runs of whitespace, keep line structure.
  Convert table rows into "Field: Value" lines, one fact per line - this is critical
  because MiniLM retrieves poorly on dense numeric tables (PRD risk R4). Keep headings
  as markdown # / ## markers so the chunker can see structure.
- fetch_all(pages, cache_dir): if cache_dir/<slug>.txt exists, use it unless
  force=True; otherwise fetch and write the raw text. One file per source URL.
- main(): run fetch_all over config.SOURCE_URLS, print a per-URL summary table
  (url, status cached/fetched/failed, n_words), and EXIT NON-ZERO if any of the 5
  mandatory scheme URLs failed or produced under 300 words. Partial failure must be
  visible, never silent.

Add argparse: --force, --cache-only. Keep the ingestion entry point importable so a
later phase can call the steps individually.

After implementing, run it and report the summary table. Then tell me which pages
produced under 300 words - I need to know before we design chunking.
```

### Manual Verification

```powershell
python -m ingest
Get-ChildItem data\raw | Select-Object Name, Length
Get-Content data\raw\hdfc_large_cap_direct_growth.txt -TotalCount 40
```

> **As-built note (Phase 2 completed 2026-09-28).** The "prefer trafilatura" guidance
> above is superseded by what the data turned out to be. These pages are Next.js apps:
> the facts live in a `<script id="__NEXT_DATA__">` JSON payload, and trafilatura's
> recovered "main content" is almost entirely performance tables — historic returns, a
> category-rank table, and peer-comparison returns — which is exactly what C3 forbids.
> `ingest.py` therefore reads a **whitelisted** set of JSON fields and flattens them to
> `Field: Value` lines, and the HTML prose path survives only as a fallback that drops
> markdown tables. `EXCLUDED_PERFORMANCE_FIELDS` in `ingest.py` is the enforcement
> boundary. Corpus is ~191–215 words per scheme, hence `config.MIN_WORDS_PER_SOURCE = 60`
> plus a 6-fact coverage assertion, rather than the 300-word rule of thumb.
> Cache files are named `<scheme_slug>.txt`, e.g. `hdfc_large_cap_direct_growth.txt`.

**Now do the human part.** Open 2–3 files and answer:
- Are the fee fields (expense ratio, exit load, minimum SIP) present as readable lines?
- Are the riskometer and benchmark present?
- How many words per page? Any page under 300?
- Do the five pages use near-identical section headings?

### Exit Gate

- [ ] All 5 scheme URLs produce a non-trivial cached file
- [ ] Failure exits non-zero with a visible summary
- [ ] Re-running does **not** re-fetch (cache hit) unless `--force`
- [ ] You have written down your observations about the data — these go into `CHUNKING.md`

**Commit:** `feat: phase 2 fetch and normalize`

---

## 7. Phase 3 — Chunking (MANDATORY, HUMAN GATED)

**Goal:** Choose a chunking strategy **from inspected data**, write it down, and produce human-readable chunks. The brief requires the rationale to exist before embedding code runs.

**Files to create:** chunking logic inside `ingest.py`, `data/chunks.txt`, `CHUNKING.md`.

### Cursor Prompt — Phase 3

```md
Read PRD.md section 10 and ARCHITECTURE.md section 4 first.

Task: Phase 3 - chunking. Read data/raw/*.txt yourself before writing the chunker,
then implement it.

Step A - inspect first. Programmatically report for each raw file: word count, the
heading structure, and how many "Field: Value" lines exist. Show me the report. This
report is the evidence that belongs in CHUNKING.md.

Step B - implement the Chunk dataclass from ARCHITECTURE.md 4.3 with all 11 fields.
Chroma metadata must be str/int/float/bool only - coerce None to "".

The strategy, per ARCHITECTURE.md 4.2:
- Section-aware first: split on markdown headings into sections. Never merge across
  different top-level sections.
- Within a section, sentence-pack sentences into chunks of CHUNK_TARGET_WORDS (200),
  hard-capped at CHUNK_MAX_WORDS (220).
- Overlap CHUNK_OVERLAP_PCT (0.20) at SENTENCE boundaries only - never mid-sentence.
- Drop chunks under CHUNK_MIN_WORDS (25).
- "Field: Value" lines are atomic: never split one across chunks.
- Set char_start/char_end against the normalized source text, and content_hash = sha1 hex.

Step C - token assertion. Add assert_chunk_fits(text, tokenizer) that raises if the
real tokenizer produces more than config.MAX_EMBED_TOKENS tokens. This is a hard
requirement - the embedder truncates silently and we would lose facts without an error.

Step D - write data/chunks.txt. Human-readable. Each chunk as:

  === CHUNK 004 ===
  source_url: https://...
  page_title: ...
  heading_path: Fees and charges > Expense ratio
  scheme_name: ...
  scheme_category: large_cap
  source_type: scheme_page
  chunk_index: 4
  char_start: 1180  char_end: 2260
  content_hash: 3f2a...
  fetched_at: 2026-09-28T10:14:03+05:30
  tokens: 187
  ---
  <chunk text>

  (blank line between chunks)

Step E - add a --inspect mode that runs chunking and writes chunks.txt WITHOUT
embedding anything. This is how we review before committing to the strategy.

Step F - write CHUNKING.md with these sections: Data observed (the Step A report, with
real numbers), Strategy chosen, Why it suits this data, Parameters table (size,
overlap, minimum), Metadata kept per chunk and why, Alternatives rejected (fixed-size
window, whole-page, paragraph split) and why they lose field/label pairs, and the
256-token constraint with the real token histogram from chunks.txt.

Do not embed. Do not touch Chroma. Do not write app code.
```

### Manual Verification — this is the gate

```powershell
python -m ingest --inspect
Get-Content data\chunks.txt -TotalCount 60
(Select-String -Path data\chunks.txt -Pattern '=== CHUNK').Count
Select-String -Path data\chunks.txt -Pattern 'tokens: (\d+)'
```

Then **read `chunks.txt` yourself.** Check specifically:

| Check | Pass condition |
|---|---|
| Field integrity | "Expense ratio: 1.16%" and its label are in the same chunk |
| Token ceiling | Max `tokens:` value ≤ 256 (target ~200) |
| Overlap sanity | Consecutive chunks in a section share some text; nothing split mid-sentence |
| No cross-section merges | A chunk never spans two top-level headings |
| Coverage | Each of the 6 core facts (expense ratio, exit load, min SIP, ELSS lock-in, riskometer, benchmark) is retrievable in at least one chunk |

**Tune here if needed** — change the numbers in `config.py`, re-run `--inspect`, re-read. This is the cheap moment to iterate; after Phase 4 it is not.

### Exit Gate

- [ ] `CHUNKING.md` written with real numbers from the inspected data, and states why the strategy suits *this* data
- [ ] `data/chunks.txt` exists and is genuinely readable
- [ ] All six checks above pass
- [ ] `config.py` numbers match what `CHUNKING.md` documents

**Commit:** `feat: phase 3 chunking strategy and chunks`

**Do not proceed until this gate is signed off.** This is milestone M1 in the PRD.

> **As-built note (Phase 3 completed 2026-09-28).** The chunking strategy in the cursor
> prompt was written before the data was inspected, and the observed data changed it in
> one important way: **one chunk per `##` section** (a section is 14–68 words, always
> under the 220-word cap, so nothing ever spills and `CHUNK_OVERLAP_PCT` is inert on
> this corpus). `CHUNK_MIN_WORDS` dropped 25 → 10 so the 14–20 word scheme-objective
> section survives. `CHUNKING.md` documents all of it with the real 25-chunk token
> histogram (34–139 tokens, median 68, max 139 ≤ 256). The whitespace-joined chunk text
> is `# <scheme>` + `## <heading>` + body; ids are `{scheme_slug}::{index:03d}`. The
> `tokens:` line per block was kept from the Prompt's Step D, and the `# fetched_at:`
> line is now written to (and read from) each cache file so the C7 "Last updated from
> sources" stamp is honest on cache reads. `config.get_tokenizer()` now prefers the
> local HF cache (`local_files_only=True`) so the tokenizer never stalls on the hub's
> metadata HEAD-check on a flaky network; a missing cache still triggers a one-time
> download. `--inspect` implies `--cache-only`.

---

## 8. Phase 4 — Embed & Persist

**Goal:** Vectors on disk, ingested once.

**Files to modify:** `ingest.py`.

### Cursor Prompt — Phase 4

```md
Read ARCHITECTURE.md sections 4.2, 4.5, 4.4 first.

Task: Phase 4 - complete Stage 1: embed chunks and persist to ChromaDB. Chunking
already exists in ingest.py from Phase 3 and is signed off. Do not change the chunking
strategy; if you find a problem, stop and report it instead of editing.

- Load the embedder ONCE via sentence_transformers.SentenceTransformer(config.EMBED_MODEL).
  Assert the loaded model's max_seq_length is <= 256 and warn loudly if the model
  reports a different value than expected.
- embed_chunks(texts, batch_size=32) -> ndarray (n, 384) float32, normalize_embeddings=True.
  Assert the shape's second dimension == config.EMBED_DIM.
  Assert no all-zero vector (a zero vector means the model failed silently).
- open_collection() -> chromadb.PersistentClient(path=config.CHROMA_PATH) and
  get_or_create_collection(name=config.COLLECTION, metadata={"hnsw:space": "cosine"},
  embedding_function=None). The None is deliberate - we embed ourselves and pass
  vectors in. Add a docstring saying so.
- store_chunks(chunks, vectors): upsert with deterministic ids
  f"{scheme_slug}::{chunk_index:03d}", documents=chunk.text, metadatas=<all 11 Chunk
  fields minus scheme_slug, coerced to str/int>. Upsert not add, so re-runs are idempotent.
- main(): add --force-rebuild which deletes the collection first. Print final counts:
  chunks produced, chunks stored, collection.count(), and the token histogram max.

After running, print collection.count() and the max token count. Tell me if the
collection count does not equal the number of chunks in data/chunks.txt.
```

### Manual Verification

```powershell
python -m ingest
python -m ingest
python -c "import chromadb; c=chromadb.PersistentClient(path='./chroma_db').get_collection('mf_faqs'); print(c.count()); print(c.peek(limit=1)['metadatas'])"
```

### Exit Gate

- [ ] `chroma_db/` created and `collection.count() == ` number of `=== CHUNK` blocks in `chunks.txt`
- [ ] Running `python -m ingest` twice does not duplicate records (count stays the same)
- [ ] Every stored metadata dict has all 11 fields, none `None`
- [ ] `peek()` shows a real `Field: Value` fact in a document
- [ ] Importing the app later does **not** trigger ingestion (verify in Phase 8)

> **As-built note (Phase 4 completed 2026-09-28).** `load_embedder()` mirrors the
> tokenizer's `local_files_only`-first pattern so the demo never stalls on a hub
> HEAD-check; a missing model still downloads once (weights were not in the cache at
> phase start — only the tokenizer was). Counts verified: chunks=25, stored=25, and
> `collection.count()` stays 25 across a second `python -m ingest` run (upsert is
> idempotent). `embed_chunks` returns an L2-normalized (25, 384) float32 array and
> asserts dimension and non-zero. All 11 metadata fields are present with no `None`.

**Commit:** `feat: phase 4 embed and persist`

---

## 9. Phase 5 — Retrieval

**Goal:** A question becomes a ranked, filtered, de-duplicated context set.

**Files to create:** `retrieve.py`.

### Cursor Prompt — Phase 5

```md
Read ARCHITECTURE.md section 5.3 first.

Task: Phase 5 - implement retrieve.py. Query embedding, Chroma search, score floor,
MMR re-rank, context assembly. No LLM yet; answer.py does not exist.

- Single shared embedder accessor. Import it from one place only, so chunks and
  queries provably use the same model instance.
- dataclass Hit(document: str, metadata: dict, similarity: float)
- dataclass RetrievalResult(hits: list[Hit], confident: bool, newest_fetched_at: str)
- retrieve(question, top_k=config.TOP_K, score_floor=config.SCORE_FLOOR,
           mmr_lambda=config.MMR_LAMBDA) -> RetrievalResult:
    * embed the question, normalize
    * collection.query(n_results=top_k*3, include=["documents","metadatas","distances"])
    * cosine space means similarity = 1.0 - distance. Do not mix this up.
    * drop hits below score_floor
    * if nothing survives, return RetrievalResult(hits=[], confident=False,
      newest_fetched_at="") - the caller will say "not in sources"
    * run greedy MMR down to top_k
- mmr(hits, k, lambda_, embed_fn) -> list[Hit]: implement greedy Maximal Marginal
  Relevance yourself, ~15 lines. Score each candidate as
  lambda * sim(query, c) - (1 - lambda) * max sim(c, already_selected).
  Re-embed candidates with the same model for the pairwise similarity.
  Add a docstring noting Chroma has no built-in MMR and we avoided adding a framework.
- build_context(result) -> tuple[str, str]: assemble numbered [SOURCE n] blocks each
  containing page_title, heading_path, URL, FETCHED timestamp, and the text; separated
  by a --- rule. Also return the max fetched_at across hits, which is what becomes the
  "Last updated from sources:" value. The LLM must never write that date itself.
- A __main__ block that runs 3 probe questions and prints the top hit's
  source_url, heading_path, and similarity for each:
    "What is the lock-in period for the ELSS fund?"
    "What is the exit load?"
    "asdfghjkl nonsense query"

After implementing, run it and report the three probes. I need to see the similarity
scores so we can sanity-check config.SCORE_FLOOR.
```

### Manual Verification

```powershell
python -m retrieve
```

### Exit Gate

- [ ] "ELSS lock-in" returns the lock-in chunk at the top
- [ ] "exit load" returns an exit-load chunk
- [ ] The nonsense query returns `confident=False` with no hits
- [ ] Similarity scores look sane (real hits well above 0.25, junk below it)
- [ ] If the ELSS query fails, **stop and fix here** — do not move on and hope the LLM compensates

**Commit:** `feat: phase 5 retrieval`

> **As-built note (Phase 5 completed 2026-09-28).** The plain vector route failed its
> own gate: "What is the exit load?" had **zero** hits above the 0.25 floor (top score
> 0.12–0.15), while an embedding of "asdfghjkl nonsense query" scored 0.16 — no single
> floor separates a short lexical topic query from junk, because a two-token query
> vector is diluted by the chunk's other field tokens. Measured calibration: real
> questions 0.30–0.68, junk ≤0.16. Fix = deterministic query expansion
> (`config.QUERY_ALIASES`, canonical label + section heading appended for the query
> embedding only). Verified after expansion: all 8 core-fact phrasings → 0.35–0.74 at
> the correct section; junk unchanged ≤0.16; the three probes (ELSS lock-in 0.583 at
> `elss/Investment limits`, exit-load 0.371 at `balanced_advantage/Charges and fees`,
> nonsense `confident=False`) pass. `build_context` numbers `[SOURCE n]` blocks with
> `page_title — heading_path`, `URL`, `FETCHED`, and text; the `newest_fetched_at` is appended by code, never written by the
LLM (C7). Also fixed the `get_sentence_embedding_dimension` FutureWarning in
`ingest.load_embedder`.

---

## 10. Phase 6 — Guard Stage

**Goal:** PII never reaches Groq; advice and performance questions never reach the answer path.

**Files to create:** `guards.py`, `tests/test_guards.py`.

### Cursor Prompt — Phase 6

```md
Read PRD.md section 12 and ARCHITECTURE.md section 5.2 first.

Task: Phase 6 - implement guards.py. This is a hard-constraint module. Write the tests
in the same pass.

- Compile a list of (name, compiled regex) for PII: PAN, Aadhaar, account/folio
  number, OTP, email, phone. Follow the table in PRD 12.1 exactly. Aadhaar in the
  brief is written with spaces - accept both spaced and unspaced forms.
- pii_scan(text) -> PIIMatch(hit: bool, patterns: list[str])  - return which pattern
  names matched, never the matched value.
- classify_intent(question) -> Intent, one of ALLOWED / ADVICE / PERFORMANCE /
  OUT_OF_CORPUS. Keyword and regex based, deterministic (ADR-3):
    ADVICE: should I, should we, buy, sell, invest in, allocate, switch, redeem,
            is it good, worth it, suggest, recommend, which is better, best for me,
            my portfolio, my holdings, suitable for me
    PERFORMANCE: return, returns, CAGR, X-year return, best performing, top performing,
            alpha, ranking, ranked, vs benchmark, outperform, since inception, NAV,
            SIP returns, profit
    OUT_OF_CORPUS: a known fund-house or scheme token that is NOT in
            config.SUPPORTED_SCHEMES. Be careful - do not flag the 5 supported schemes.
            Default to ALLOWED when unsure; a false refusal is worse than a routed query.
- Order matters: PII first, then intent. A question containing both PAN and "should I"
  must be blocked as PII, not refused.
- Message constants: PII_MESSAGE (politely asks the user not to share personal
  details, mentions PAN/Aadhaar/account/OTP, and does NOT repeat their input),
  ADVICE_REFUSAL (facts-only wording + one config.EDUCATION_URLS link + an offer to
  answer a specific fact instead), PERFORMANCE_DEFLECTION (no numbers, links the
  official factsheet), OUT_OF_SCOPE_MESSAGE (lists the 5 supported schemes by name).
  All four must be facts-only, non-preachy, and short.
- GuardDecision(action: Literal["block","refuse","deflect","out_of_scope","allow"],
  message: str | None)
- run_guards(question) -> GuardDecision, per ARCHITECTURE.md 5.2.
- No logging of raw input anywhere. If you add a log line, log the decision code and
  a sha256 prefix of the question, at most.

tests/test_guards.py - pytest, at least these cases:
  - all 6 PII patterns block, including a real-format PAN and a 10-digit phone
  - "My PAN is ABCDE1234F and my folio is 12345678" blocks and the returned message
    does NOT contain "ABCDE1234F"
  - a PAN + "should I buy" question blocks, not refuses
  - each of the 5 supported scheme names returns ALLOWED
  - "Should I buy the ELSS tax saver fund?" -> ADVICE
  - "Which fund has the best 5-year return?" -> PERFORMANCE
  - "Expense ratio of Parag Parag Flexi Cap?" -> OUT_OF_CORPUS
  - "What is the expense ratio of HDFC Large Cap Fund Direct Growth?" -> ALLOWED
  - every refusal message is under 4 sentences and contains no numeric return figure

Run pytest and report the result.
```

### Manual Verification

```powershell
pytest tests\test_guards.py -v
python -c "from guards import run_guards; print(run_guards('Should I buy the ELSS fund?'))"
python -c "from guards import run_guards; print(run_guards('My PAN is ABCDE1234F'))"
```

### Exit Gate

- [ ] All guard tests pass
- [ ] No refusal message contains the user's input back to them
- [ ] No supported scheme is falsely refused
- [ ] **Grep the repo for any logging of raw question text and remove it**

**Commit:** `feat: phase 6 guards`

> **As-built note (Phase 6 completed 2026-09-28).** 13/13 `tests/test_guards.py` pass.
> Two test expectations were corrected to match the spec, not the code: the phone
> pattern per ARCHITECTURE 5.2 is contiguous-10-digit (`+91-9876543210`, not spaced
> `98765 43210`), and intent order is ADVICE → PERFORMANCE → OUT_OF_CORPUS, so "What is
> the NAV of SBI Magnum?" correctly **deflects** (performance beats out-of-scope).
> `PERFORMANCE_DEFLECTION` links `config.FACTSHEET_URL` (`https://www.hdfcfund.com`,
> verified to resolve; edge 403s bots but opens in browsers) since no factsheet URL
> existed in config. `safe_prefix()` is the only permitted logging form for user input;
> no code logs raw questions.

---

## 11. Phase 7 — Answering & Answer Contract

**Goal:** Grounded answers that satisfy the contract every time, or an honest failure.

**Files to create:** `answer.py`, `tests/test_contract.py`.

### Cursor Prompt — Phase 7

```md
Read PRD.md section 11 and ARCHITECTURE.md section 5.5 first.

Task: Phase 7 - implement answer.py. Build the prompt, call Groq, validate the answer
deterministically, repair once, then fall back. Write the contract tests in the same pass.

- Answer dataclass: text, source_url, source_title, last_updated, hits (for the UI
  sources expander), mode (Literal["generated","extractive","not_in_sources","refused"]).
- SYSTEM_PROMPT in config.py. Rules, in order:
    1. Answer ONLY from the [SOURCE n] blocks provided. If the answer is not there,
       say you could not find it in the source pages. Never use prior knowledge.
    2. 1 to 3 sentences. No preamble, no bullets, no sign-off, no offer of further help.
    3. End with exactly one line "Source: <url>" using a URL that appears in a SOURCE block.
    4. Never state, compute, or compare a return or performance figure. Never say
       "you should", "we recommend", or any suitability judgement.
    5. Never repeat a personal identifier, even if one appears in the context.
    6. Facts only. If asked for an opinion, say you do not give investment advice.
- call_groq(question, context) -> str: use the groq SDK, model=config.GROQ_MODEL,
  temperature=0, max_tokens=250. Use require_api_key(). Catch every Groq error and
  return None - never propagate, never fabricate.
- SENTENCE_SPLITTER: a decimal-safe regex splitter. A naive split on "." breaks
  "1.16% p.a." Use the pattern in ARCHITECTURE.md 5.5. Add a unit test asserting
  "The expense ratio is 1.16% p.a. Exit load is nil." counts as 2 sentences, not 4.
- validate_answer(text, hits) -> tuple[bool, list[str]]: returns ok and the list of
  violations. Checks, in this order: <= 3 sentences; exactly one http URL present;
  that URL's domain is in config.ALLOWED_DOMAINS AND the exact URL appeared in the
  retrieved hits; no performance claim in a factual answer; no PII pattern echoed.
- repair once: if validation fails, re-prompt with the violations named explicitly and
  the retrieved context repeated. If it fails again, fall back.
- extractive_fallback(hits): take the first 2 sentences of the top hit's text, prepend
  "From the source page:", and include its URL and fetched_at. Set mode="extractive".
- answer_question(question) -> Answer: the orchestrator. It does NOT call guards -
  app.py calls guards first and returns early on refuse/deflect/block. This keeps
  ingest/guards/answer concerns separated.
- not_in_sources handling: if retrieval was not confident, return mode="not_in_sources"
  with a message that lists the 5 supported schemes and says the topic is not in the
  source pages. Do not call the LLM at all in this case.
- Degraded mode: if call_groq returns None, use extractive_fallback and set a visible
  notice string explaining generation is unavailable.

tests/test_contract.py - pytest with a fake LLM (monkeypatch call_groq) so tests need
no API key:
  - a good answer passes validation
  - a 4-sentence answer is rejected
  - an answer with 0 URLs is rejected
  - an answer with 2 URLs is rejected
  - an answer citing a non-allow-listed domain is rejected
  - an answer citing a URL that is not in the retrieved hits is rejected
  - an answer echoing a PAN is rejected
  - the decimal sentence splitter counts correctly
  - repair is attempted exactly once, then fallback

Run pytest and report.
```

### Manual Verification

```powershell
pytest tests\test_contract.py -v
```

Then, with a real key in `.env`:

```powershell
python -c "from retrieve import retrieve; from answer import answer_question; print(answer_question('What is the expense ratio of the HDFC Large Cap Fund Direct Growth?').text)"
```

### Exit Gate

- [ ] All contract tests pass **without an API key** (fakes)
- [ ] A real query returns ≤ 3 sentences, 1 link, and `Last updated from sources:`
- [ ] Deleting `GROQ_API_KEY` produces the extractive fallback, not a crash
- [ ] The LLM never writes the timestamp — verify the code appends it

**Commit:** `feat: phase 7 answering and contract`

> **As-built note (Phase 7 completed 2026-09-28).** 17/17 `tests/test_contract.py` pass
> with a faked LLM (no API key, no network), and the full offline suite is 30/30.
> `answer.py` defines the `SYSTEM_PROMPT` in config.py (rules 1–6), the `Answer`
> `Answer` dataclass, `call_groq` (every failure → None, never propagates), decimal-safe
> `_SENT` splitter, `validate_answer` (≤3 sentences; exactly one http URL; domain in
> `ALLOWED_DOMAINS` AND the exact URL present in the retrieved hits; no performance
> claim; no PII echo), repair exactly once with the violations named, then
> `extractive_fallback` (first two sentences of the top chunk, labelled
> "From the source page:", with URL and `fetched_at`). `answer_question` does NOT call
> guards (app.py owns the early-exit path) and returns `mode="not_in_sources"` without
> touching the LLM when retrieval is not confident. Two contract details worth noting:
> the performance-claim regex is deliberately sharper than the intent gate so grounded
> fact "Benchmark: NIFTY 500 Total Return Index" is not flagged (a claim needs an
> explicit verb or a % near "return(s)"); and extractive fallback of a line-format
> chunk rolls up the whole chunk because the sentence splitter treats it as one unit —
> the phrase "first two sentences" in the spec applies to prose chunks. Live gate items
> (real query ≤3 sentences/1 link/timestamp; LLM never writes the timestamp) still
> pending a real `GROQ_API_KEY`; the no-key path was verified live to produce the
> extractive fallback with the degraded notice instead of a crash.

---

## 12. Phase 8 — Streamlit UI

**Goal:** The brief's "tiny UI": welcome line + 3 example questions + disclaimer. No retrieval logic in the UI file.

**Files to create:** `app.py`, `DISCLAIMER.md`.

### Cursor Prompt — Phase 8

```md
Read ARCHITECTURE.md section 6 and PRD.md FR-19..FR-22 first.

Task: Phase 8 - build the Streamlit UI in app.py. The UI is a thin shell. It must not
contain any embedding, search, or prompt logic - it calls guards, then answer.

- page config, wide layout, a short title.
- Load the embedder and Chroma client with @st.cache_resource. Importing the app must
  NOT trigger ingestion. If the collection is missing or count()==0, show a clear
  message: "Corpus not built yet. Run: python -m ingest" and stop.
- Disclaimer text lives in DISCLAIMER.md. app.py reads it and renders it at the top of
  the page and in the sidebar. Use the exact wording from PRD Appendix B. It must be
  persistently visible, not a dismissible banner.
- First screen: welcome line, the disclaimer, and exactly 3 example questions from
  PRD Appendix C as st.button widgets that submit their question.
- Chat history in st.session_state.messages. Each assistant message renders:
  body, the single citation as a hyperlink labelled with the source page title,
  "Last updated from sources: <ts>" in muted small text, and a st.expander
  "Sources (n)" listing each retrieved chunk's page title, heading path, and similarity.
- On submit: run guards.run_guards(question) first.
    block / refuse / deflect / out_of_scope  -> render that message in a distinct style
                                                and do NOT call the LLM
    allow                                    -> call answer_question and render the Answer
- Refusals and deflects must look visibly different from normal answers so a reviewer
  sees the guard working.
- Add a sidebar "About this demo" with the AMC, the 5 schemes, the source count, and a
  link to the GitHub-flavored source list.
- Do not store rejected input in session state. Do not log it.

Write DISCLAIMER.md with the exact text from PRD.md Appendix B plus a short note on
where it appears in the UI.
```

### Manual Verification

```powershell
streamlit run app.py
```

Check in the browser:

- [ ] First screen shows welcome + 3 example buttons + disclaimer
- [ ] "What is the lock-in period for the ELSS fund?" returns 3 years + 1 link + timestamp
- [ ] "Should I buy the ELSS?" shows a refusal with an education link and no numbers
- [ ] "My PAN is ABCDE1234F" is blocked and the value is not echoed
- [ ] Sources expander shows page title + heading path
- [ ] Restarting the app does not re-embed anything (watch the console)
- [ ] With no collection, the app shows the "run python -m ingest" message instead of crashing

**Commit:** `feat: phase 8 streamlit ui`

---

## 13. Phase 9 — Test Suite & Sample Q&A

**Goal:** Prove the demo works, end to end, and produce the sample-QA deliverable.

**Files to create:** `tests/test_chunks.py`, `tests/test_retrieval.py`, `tests/run_sample_qa.py`, `sample_qa.md`.

### Cursor Prompt — Phase 9

```md
Read PRD.md section 19 and ARCHITECTURE.md section 11 first.

Task: Phase 9 - remaining tests plus the sample-QA generator.

tests/test_chunks.py:
  - re-tokenize EVERY chunk in data/chunks.txt with the real tokenizer and assert
    <= config.MAX_EMBED_TOKENS. Report the max. This guards the silent-truncation risk.
  - assert every chunk has all 11 metadata fields, none None/empty for the required ones
  - assert the chunk count in chunks.txt equals the Chroma collection count
  - assert each of the 6 core facts (expense ratio, exit load, minimum SIP, ELSS lock-in,
    riskometer, benchmark) appears in at least one chunk's text

tests/test_retrieval.py:
  - "lock-in" style query retrieves a lock-in chunk
  - a nonsense query returns confident=False
  - no supported scheme name is misrouted to OUT_OF_CORPUS

tests/run_sample_qa.py:
  - the 9 questions from PRD Appendix D, in order
  - for each: run guards, then answer, and record action, answer text, citation URL,
    sentence count, and last_updated
  - re-check the answer contract on every non-refusal answer
  - print a pass/fail table AND write sample_qa.md in a readable form:
      ### Q1. What is the expense ratio of the HDFC Large Cap Fund - Direct Growth?
      **A:** <answer>
      **Source:** <url>
      **Last updated from sources:** <ts>
      **Sentences:** 2  **Mode:** generated
  - exit non-zero if any contract check fails, so this doubles as the demo smoke test

Mark tests that need GROQ_API_KEY with pytest.mark.skipif and make sure the offline
suite (guards, contract, chunks, retrieval) passes with no key at all.
```

### Manual Verification

```powershell
pytest -q
python -m tests.run_sample_qa
Get-Content sample_qa.md
```

### Exit Gate

- [ ] `pytest -q` is green with **no API key set**
- [ ] `python -m tests.run_sample_qa` is green **with** the key
- [ ] `sample_qa.md` has 9 Q&A entries, each with a link
- [ ] The PII entry shows a block, and the PAN is not reproduced in the file

**Commit:** `feat: phase 9 tests and sample qa`

---

## 14. Phase 10 — Documentation Deliverables

**Goal:** Close out the four written deliverables from the brief.

**Files to create/modify:** `README.md`, `SOURCES.md`, `DISCLAIMER.md` (already exists), update `data/chunks.txt` reference.

### Cursor Prompt — Phase 10

```md
Read PRD.md section 16 (deliverables) and 19 (acceptance criteria) first.

Task: Phase 10 - write the documentation deliverables. Do not invent facts. Every
number, path, and command must match the code that exists. Verify each command by
reading the actual argparse flags in ingest.py, retrieve.py, and app.py before you
document it.

1. README.md, replacing the Phase 0 stub. Sections:
   - What this is (2 sentences) and the facts-only disclaimer
   - Scope: HDFC AMC, the 5 schemes with their categories, and the 5 source URLs
   - Architecture summary: the two stages, one diagram, 5 lines of prose
   - Setup: exact commands, in order, including venv creation, pip install, .env copy,
     and the explicit statement that .env is never committed
   - Running ingestion, running the app, running the tests - real flags only
   - The chunking strategy in 6 lines, pointing at CHUNKING.md for detail
   - Known limits - be honest and specific. Must include: values change and must be
     verified on the official factsheet; the source-policy decision from PRD OQ-1
     (Groww pages as the enumerated corpus, cross-checked against HDFC AMC/AMFI);
     the corpus is only 5 schemes; no returns are computed; nothing is stored about users
   - Project structure (the tree from ARCHITECTURE.md 15)

2. SOURCES.md - a table of the 5 scheme URLs with scheme, category, source_type, and
   when it was fetched, plus the official reference domains actually used, plus a note
   on which fact came from which page. Also emit SOURCES.csv with the same rows.

3. sample_qa.md is already generated by Phase 9. Read it and do not fabricate extra
   entries. If any answer is wrong or uncited, report it to me instead of editing the
   file by hand.

4. Confirm DISCLAIMER.md matches what app.py actually renders. If it does not, fix
   app.py to match the PRD wording.
```

### Manual Verification

- [ ] Every command in the README runs exactly as written, in a fresh shell
- [ ] README lists all 5 schemes and 5 URLs accurately
- [ ] Known limits mentions the source-policy decision and stale-values risk
- [ ] `SOURCES.csv` opens cleanly and has 5 rows
- [ ] `sample_qa.md` was machine-generated, not hand-edited

**Commit:** `docs: phase 10 deliverables`

---

## 15. Phase 11 — Rehearsal & Backup

**Goal:** The demo works on an unfamiliar laptop, with no network, on the day.

### Steps

| # | Action | Command / check |
|---|---|---|
| 1 | Fresh-clone rehearsal | Clone the repo to a temp dir, follow the README verbatim, note every step that surprises you |
| 2 | Offline retrieval test | Turn off Wi-Fi, start the app, ask a factual question. Retrieval must still work (degraded extractive mode) |
| 3 | API failure drill | Temporarily set `GROQ_API_KEY=bad` and confirm the app degrades instead of crashing |
| 4 | Cold-start timing | `Measure-Command { streamlit run app.py }` — note the first-load time |
| 5 | Full acceptance sweep | Walk PRD §19's 12 criteria, one by one, in front of the app |
| 6 | Demo video backup | Screen-record a ≤3-minute walkthrough: 1 factual answer with its citation, 1 refusal, 1 PII block, 1 sources expander. This satisfies deliverable D1 if hosting fails |
| 7 | Tag the milestone | `git tag -a v1.0-demo -m "MF facts-only RAG chatbot demo"` |

### Exit Gate

- [ ] Fresh clone follows the README without assistance
- [ ] Retrieval works with the network off
- [ ] A bad API key degrades gracefully
- [ ] All 12 PRD §19 criteria pass
- [ ] A ≤3-minute video exists as a backup
- [ ] `.env` is confirmed absent from `git ls-files`

**Commit/Tag:** `chore: phase 11 demo rehearsal`

---

## 16. Master Acceptance Checklist

Verify every line before calling the milestone done. This mirrors PRD §19.

| # | Criterion | Phase |
|---|---|---|
| 1 | `python -m ingest` populates `chroma_db` and writes `data/chunks.txt` | 3, 4 |
| 2 | Starting the app does not re-run ingestion | 4, 8 |
| 3 | Exit-load question → correct value, ≤3 sentences, 1 link, timestamp | 5, 7 |
| 4 | ELSS lock-in question → 3 years, ≤3 sentences, 1 link | 5, 7 |
| 5 | Minimum-SIP question → amount, ≤3 sentences, 1 link | 5, 7 |
| 6 | "Should I buy the ELSS?" → refusal, facts-only, education link, no numbers | 6, 8 |
| 7 | "Best 5-year return?" → no numbers, official factsheet link | 6, 8 |
| 8 | PAN input → blocked, not echoed, not logged | 6 |
| 9 | Out-of-scope scheme → message listing the 5 supported schemes | 6 |
| 10 | First screen: welcome, 3 examples, "Facts-only. No investment advice." | 8 |
| 11 | `README.md`, `SOURCES.md`, `sample_qa.md`, `DISCLAIMER.md`, `CHUNKING.md` committed | 10 |
| 12 | `.env` gitignored and uncommitted | 1, 11 |

---

## 17. Time-Budget Guidance

| If you have… | Do this | Skip |
|---|---|---|
| 1 day | Phases 0–7, then a command-line demo runner instead of Streamlit | Phase 8 polish (use `run_sample_qa.py` output as the demo), Phase 10 verbosity |
| 2 days | Phases 0–10 | Phase 11 except the video |
| 1 week | Everything, plus tuning the score floor and adding a second fact sheet source per scheme | — |

**Never cut, at any time budget:** Phase 3 (chunking decision, a brief requirement), Phase 6 (PII and advice guards, C2/C4), Phase 7's post-validation (C5/C6).

---

## 18. Troubleshooting Reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `chroma_db` empty after ingest | Collection name or path mismatch | Check `config.CHROMA_PATH` vs the printed path; delete `chroma_db/` and re-run with `--force-rebuild` |
| All similarity scores near zero | Query and chunks embedded with different models | Confirm a single embedder accessor is used by both stages (ARCHITECTURE.md 5.3) |
| Some facts never retrieved | Chunk over 256 tokens, or a table not flattened | Check `tokens:` values in `chunks.txt`; re-run `--inspect` after flattening |
| Advice question gets answered | Guard order or keyword list wrong | `pytest tests/test_guards.py -v`; the intent check must run before retrieval |
| Answer is 5 sentences | LLM ignoring the rule | This is what post-validation is for — confirm `validate_answer` actually runs on every response path |
| Two citation links | LLM adding an extra source | Post-validation should reject; check the repair path runs |
| Timestamp looks wrong | LLM writing its own date | The date must be appended by code from `newest_fetched_at`, not by the prompt |
| App re-embeds on restart | Ingestion called from `app.py` or an import side effect | Move to `if __name__ == "__main__"`; check for module-level side effects |
| `ModuleNotFoundError: groq` | venv mismatch | Activate the venv used for the install; re-run `pip install -r requirements.txt` |
| Fetch returns 403 | Bot protection on a source page | Use the `--cache-only` path; capture the raw text once from a browser and commit it to `data/raw/` |
| Rate limited by Groq | Too many demo queries in a row | Keep the app's retry single-shot and rely on the extractive fallback |

---

## 19. Appendix — Repair Prompts

**If a phase's gate fails, paste this shape to Cursor:**

```md
Phase <N> exit gate failed. Here is exactly what happened:

Command: <command>
Output: <paste the full error or wrong output>

Expected per IMPLEMENTATION.md Phase <N>: <the specific expectation>

Diagnose the root cause before changing anything. Do not weaken a test to make it
pass, and do not relax a threshold in config.py to hide the problem. If the
architecture itself is at fault, say so explicitly and propose the change - do not
work around it silently. Fix, then re-run the full Phase <N> verification and show me
the output.
```

**If Cursor drifts out of scope:**

```md
Stop. You are adding scope that is not in Phase <N>. Re-read IMPLEMENTATION.md
Phase <N> and revert the extra work. Then re-implement Phase <N> exactly as
specified, and list the files you changed.
```

**If Cursor contradicts a hard constraint (C1–C8):**

```md
This change violates PRD constraint <C-number>. <State the constraint and why it
matters.> Revert and implement it in a way that satisfies the constraint. The
constraint is non-negotiable even if it makes the implementation less elegant.
```

---

*Next: run Phase 0. Do not start Phase 4 (embedding) until Phase 3's human gate is signed off — that gate is a brief requirement, not a preference.*
