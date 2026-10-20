# SIP Chatbot — performance & accuracy plan

_Real-talk design note for the Storage AI assistant (Text-to-SQL). Feeds SCPSB-946 (PGvector POC) and the AI stream._

## Baseline (what we actually have)

NL question → `qwen2.5:3b` (local, CPU) → a SELECT (validated against the USM schema) → run → format the answer, with self-correction on failure. Two cost axes:

- **Latency** — a 3B model on CPU is ~20–40 s per LLM call; self-correction and answer-formatting add more calls.
- **Accuracy** — a small model picks the wrong tables/columns and writes shaky SQL; it leans on the validator + retries.

**Honest ceiling:** no prompt trick turns a 3B CPU model into GPT-4. Real gains come from three moves: (a) make the model's job *easier* with better context, (b) *avoid* the model when we already know the answer, (c) better *compute*. Ranked by value ÷ effort below — but first, the non-negotiable guardrails.

## 0. Guardrails — do NOT degrade other consumers

The model and the DB are **shared**. Capacity/performance **forecasting (SCPSB-958)** and the **analytics** views read the same DB, and anything that calls the LLM shares the same Ollama on the same CPU. Every chatbot change is bound by:

- **DB changes are additive-only.** The data dictionary is a *new* table (`usm_dictionary`); curated views are *new* views. Never alter, rename, or drop an existing USM table/column that the collectors, forecasting, or analytics read. pgvector goes in its own extension/schema, not mixed into the analytics tables.
- **Isolate chat compute.** Chat already has `CHAT_MAX_CONCURRENT` so it can't starve the API. RAG/semantic-cache add embedding calls on the *same* CPU Ollama — so **precompute** embeddings offline (the dictionary + golden set are static → embed once, not per request), and throttle so chat can never contend with a forecasting/analytics model call. If load grows, give chat its own Ollama instance/queue.
- **Chat stays read-only** (SELECT-only validation already enforces this), so it physically cannot corrupt data other services depend on.
- **Regression-gate it:** run forecasting + the analytics endpoints on a fixed window before/after any change and confirm the outputs are unchanged. "Improve the chatbot" is only done if nothing else moved.

## 1. Tag the data — a semantic layer / data dictionary  ← your "tag data for efficiency", and the biggest win

The #1 reason a small model writes wrong SQL is it doesn't know what the columns *mean*: `cap_tb`, `svm`, `reduction_ratio`, vendor codes are opaque. Fix that with a **data dictionary**:

- For every table + column: a plain-English description, unit, synonyms ("usable" = `cap_tb`), example values, and the FK relationships.
- Tag each array/volume with its business attributes it's queried by: vendor, CSP, datacenter, technology (block/file), tier.
- Store it as a table in the DB (`usm_dictionary`) so it's versioned with the schema and queryable.

This is pure metadata (no row rewrites), needs **no migration**, and lifts small-model Text-to-SQL accuracy more than anything else — because it's then *retrieved into the prompt* (next item).

## 2. PGvector RAG — schema linking + few-shot  (SCPSB-946)

Embed the dictionary entries **and** a library of golden `(question → vetted SQL)` pairs into pgvector. At query time: embed the question → retrieve the top-k relevant tables/columns (**schema linking**) + top-k similar examples (**few-shot**) → inject only those into the prompt.

- Why it works: small models fail at *picking the right tables from a big schema* and at *syntax*; a trimmed schema + relevant examples fix both. Schema linking also **shrinks the prompt → faster**.
- Embeddings run **locally** via Ollama (e.g. `nomic-embed-text`) — no egress, consistent with chat being local-only.
- **Dependency:** pgvector = Postgres. SIP is on SQL Server today, so this rides on the **SCPSB-1022 cutover to `stormart01p`**. Don't block items 1/3/4 on it — do the dictionary + views now, light up vector retrieval after the DB move.

## 3. Golden-query library + curated views  (accuracy *and* latency)

Most questions cluster into a few intents (capacity by X, top-N by util, alerts by severity, host/volume lookup). For each:

- A **curated view** that pre-joins/pre-aggregates the shape, so the model targets one simple view instead of a 4-table join → far fewer errors + faster queries.
- The golden library doubles as the few-shot source (item 2) **and** as a **router**: if a question maps cleanly to a known intent, use the vetted SQL directly and skip generation entirely.

## 4. Semantic cache  (latency for repeats)

Cache `question-embedding → SQL` (and `SQL → formatted answer`). If a new question is ~identical to a past one (cosine ≥ threshold), reuse the SQL/answer → near-zero latency. The app already has `USM_CACHE_DB`; upgrade it to a semantic cache (pgvector). Biggest *perceived*-speed win for common/repeat questions.

## 5. Prompt + pipeline tightening  (cheap)

- Feed only the retrieved tables (item 2) — smaller prompt = faster + more accurate.
- Keep the SELECT-only validation + self-correction, but **cap retries** (each is a full LLM call).
- Force SQL-only output (no prose) to cut tokens; use grammar/constrained decoding if the runtime supports it (guarantees a valid SQL shape).

## 6. Compute — the honest lever

The 3B-on-CPU latency floor is real; RAG trims the prompt but CPU stays slow. Levers: a SQL-specialized small model (a 7B coder) if RAM allows, or an **on-network GPU/accelerator**. The DGX work (the off-network POC we removed, #70) was building exactly this hardware case — capture its latency/accuracy numbers as the evidence. Recommendation: land items 1–5 first (they help with *any* model), then use measured results to justify on-network GPU if still needed.

## Sequencing
1. **Now (no migration):** data dictionary / tagging (item 1) + curated views + golden-query library (item 3).
2. **After the Postgres cutover (SCPSB-1022):** pgvector schema-linking + few-shot (item 2) + semantic cache (item 4).
3. **Re-measure**, then decide on compute (item 6).

## Measure it, or it's vibes
Track on a fixed eval set of real questions + expected answers: SQL-validity rate, answer-correctness, p50/p95 latency, cache-hit rate, self-correction rate. **Build the eval set first** — it's what tells us any of the above actually helped, and it's what a GPU business case rests on.

## Caveats
- Text-to-SQL on a small local model is never bulletproof — keep it assistive (the UI already shows the SQL for the user to verify).
- RAG quality = dictionary + golden-set quality. It's curation work, not just infra.
- pgvector ties to the DB migration; the dictionary/views/eval-set work does not — start there.
