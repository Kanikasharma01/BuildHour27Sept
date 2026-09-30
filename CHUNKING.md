# CHUNKING.md — Chunking Strategy & Rationale

**Status:** Final (signed off in Phase 3, milestone M1)
**Date:** 2026-09-28
**Applies to:** `config.py` chunking constants, `ingest.py` `chunk_document`.

> The brief requires the chunking decision to be made **after inspecting the data** and
> justified. This document is that justification, written before any embedding code ran.

---

## 1. Data Observed (Step A report)

Five source pages were fetched, extracted to `data/raw/*.txt`, and inspected. The pages
are Next.js applications; after JSON extraction the corpus is methodologically uniform:

| File | Words | Sections | Field:Value lines | Prose lines |
|---|---|---|---|---|
| `hdfc_balanced_advantage_direct_growth.txt` | 215 | 5 | 36 | 1 |
| `hdfc_elss_tax_saver_direct_growth.txt` | 191 | 5 | 36 | 1 |
| `hdfc_equity_flexi_cap_direct_growth.txt` | 198 | 5 | 36 | 1 |
| `hdfc_large_cap_direct_growth.txt` | 191 | 5 | 36 | 1 |
| `hdfc_small_cap_direct_growth.txt` | 194 | 5 | 36 | 1 |

Every document has the same five sections, in the same order:

```
# <Scheme name>
## Scheme identity      65-68 words    identity facts (ISIN, AMC, fund manager, ...)
## Charges and fees     22-40 words    expense ratio, exit load, stamp duty
## Investment limits    36-37 words    min SIP, min lump sum, lock-in
## Risk and benchmark   19-26 words    riskometer, benchmark, portfolio turnover
## Scheme objective     14-20 words    the only prose; the scheme objective
```

**Section sizes: 14-68 words. Every section is far under the nominal 200-word target.**

The six core facts the assistant must answer (expense ratio, exit load, minimum SIP,
ELSS lock-in, riskometer, benchmark) each sit in exactly **one** of these sections,
matched one-to-one to the section's topic.

---

## 2. Strategy Chosen

**One chunk per `##` section. Sections are never merged, and a section is never split.**

Each chunk = document title + `## ` section heading + the section's lines, e.g.:

```
# HDFC ELSS Tax Saver Fund - Direct Growth
## Charges and fees
Expense ratio: 1.21
Expense ratio as on: 2026-09-24
Exit load: Nil
Stamp duty: 0.005% (from July 1st, 2020)
```

Result: **25 chunks** (5 schemes × 5 sections), five per scheme, chunk ids:
`{scheme_slug}::{chunk_index:03d}` (e.g. `elss::002`).

---

## 3. Why This Suits This Data

1. **The retrieval unit matches the question target.** Every in-scope question ("What
   is the expense ratio?", "ELSS lock-in?", "Benchmark?") targets one specific section.
   Retrieval is then a crisp top-k over exactly the section that holds that fact, with
   minimal cross-topic bleed.

2. **Section headings are embedded with the facts.** A chunk containing `## Charges and
   fees` plus `Expense ratio: 1.03` matches both the lexical fact and the topical
   phrasing of a query like "what does this scheme charge".

3. **No section needs splitting, and none should be merged.** The observed 14-68 word
   range means the 200-word target and 20% overlap machinery never triggers. Merging
   sections would bundle, say, `Fund manager: Prashant Jain` into the chunk that
   answers `Expense ratio: 1.03` — diluting the centroid and blurring the citation.

4. **Citations stay precise.** One chunk = one section = one heading path. The answer
   can cite the scheme page and its exact section ("Charges and fees"), which is
   exactly the citation granularity a reviewer can check by hand.

---

## 4. Parameters

As implemented in `config.py`:

| Parameter | Value | Effect on this corpus |
|---|---|---|
| `CHUNK_TARGET_WORDS` | 200 | A soft pack target; never reached |
| `CHUNK_MAX_WORDS` | 220 | Hard cap; if a section ever exceeds it, ingest **fails loudly** (no silent spill) |
| `CHUNK_OVERLAP_PCT` | 0.20 | Inert here — applies only if the cap pushes a section across multiple chunks, which the data never does |
| `CHUNK_MIN_WORDS` | 10 | Floor against accidental fragments; the smallest observed section (14-word objective) clears it |
| `MAX_EMBED_TOKENS` | 256 | Hard model limit, asserted per chunk (below) |

**Parameter changes from the architecture default, and why (tuning is the sanctioned
mechanism of this gate):**

- `CHUNK_MIN_WORDS` 25 → **10.** The 25-word floor would have **dropped the scheme
  objective** (14-20 words) from every document — the only prose in the corpus, and a
  legitimate citable fact ("describe the scheme's objective"). Sections are atomic by
  construction (never split), so the floor protects only against junk fragments; 10 is
  still far above a 3-word stray line.
- No value was changed just to make a test pass; both choices follow from the observed
  14-68 word range.

---

## 5. Metadata Kept Per Chunk (and why)

| Field | Value example | Why |
|---|---|---|
| `source_url` | `https://groww.in/mutual-funds/...` | The one citation link (hard requirement C6) |
| `page_title` | `HDFC Large Cap Fund - Direct Growth` | Citation label in the UI |
| `heading_path` | `Charges and fees` | Fine-grained citation context + verification |
| `scheme_name` | `HDFC Large Cap Fund - Direct Growth` | Scheme scoping & out-of-corpus routing |
| `scheme_category` | `elss` | Scheme grouping in retrieval/tests |
| `source_type` | `scheme_page` | Differentiates future education/factsheet sources |
| `chunk_index` | `2` | Ordering, debuggability, id uniqueness |
| `char_start` / `char_end` | `504` / `674` | Traceability back to `data/raw/` |
| `content_hash` | `sha1:…` | Change detection on re-ingest |
| `fetched_at` | `2026-09-28T20:18:03+05:30` | Drives the `Last updated from sources:` stamp (C7) |

Chunk **ids** embed the scheme slug; all metadata are `str|int|float|bool` (Chroma
constraint) with `None` coerced to `""`.

---

## 6. Alternatives Rejected (and why they lose this data)

| Alternative | Why rejected |
|---|---|
| **Fixed-size window (e.g. 200-500 words, sliding)** | Would split `Field: Value` lines, tearing `Expense ratio:` from `1.03` — the exact "dense numeric retrieval" failure PRD risk R4 warns about. A 500-word window also exceeds the 256-token model cap, inviting quiet truncation (R8). |
| **Whole-page single chunk** | The 191-215 word pages technically fit, but one chunk mixes five topics: citation collapses to "the whole page", and a query like "exit load" must match against a centroid diluted with identity/objective fields. |
| **Paragraph split (one chunk per Field:Value line)** | ~36 near-tiny chunks per page; the `## Charges and fees` heading context is lost from each, so topical queries ("charges", "cost") lose their best cue, and the corpus fragments into many low-information units. |
| **Sentence-packing to the 200-word target** | The uniform small sections make this a no-op here; packing would merge topics and blur citations for zero retrieval benefit. Kept only as the documented behaviour *if* a future source has oversized sections. |

---

## 7. The 256-Token Constraint

`all-MiniLM-L6-v2` has `max_seq_length = 256`. Chunks longer than that are **silently
truncated by the tokenizer** — the tail never reaches the vector, the facts in it become
unretrievable, and no error is raised anywhere.

`ingest.py::assert_chunk_fits` runs inside every `Chunk` construction using the **real
tokenizer** (`config.get_tokenizer()`), so an oversized chunk fails the build instead of
silently losing facts.

**Measured distribution over the 25 chunks:**

| Statistic | Value |
|---|---|
| Min tokens | 34 |
| Max tokens | 139 |
| Median | 68 |
| Chunks over 128 tokens | 4 (all `Scheme identity`) |
| Chunks over 200 tokens | 0 |

The largest chunk (`Scheme identity`, 139 tokens) still has 117 tokens of headroom, so
this corpus is comfortably inside the model's window — the assertion is the safety net
that keeps it there as sources change.

---

## 8. How to Re-run

```powershell
python -m ingest --inspect            # chunk from cache, rewrite data\chunks.txt
python -m ingest --inspect --force    # refetch, then re-chunk
```

Review `data/chunks.txt` (human-readable, 25 blocks). Tokens are counted and asserted
at chunk-build time; the max appears on the ingest summary line.