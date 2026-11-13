![Urban Tandoor & Grill AI Restaurant Assistant](assets/Urban Tandoor AI Dining Experience.png)

# Urban Tandoor – Data Engineering Pipeline

Loads the **Menu Catalog** and **Two-Branch Inventory** workbooks (Excel **or JSON**) into **PostgreSQL**, and stores
**document embeddings** (Gemini) in the same database using **pgvector**.
Built so that next week's updated Excel file is a one-command refresh.

```
 .xlsx / .json ─► EXTRACT ──► VALIDATE ──► LOAD (upsert) ──► BUILD DOCUMENTS ──► EMBED ──► pgvector
 (data/incoming)  read+clean  fail fast    1 transaction     from the DB          only changed docs
                                                │
                                                └──► archive copy + etl_runs log
```

---------------------------------------------------------------------------------------------------

## 1. Quick start (10 minutes)

```bash
# 0. Python 3.10+ and Docker Desktop installed
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1. Database (Postgres 16 + pgvector)
docker compose up -d

# 2. Settings
cp .env.example .env            # Windows: copy .env.example .env
#    -> paste your free key from https://aistudio.google.com/apikey into GEMINI_API_KEY

# 3. Create tables (idempotent - also happens automatically on every run)
python -m pipeline init-db

# 4. Check the Excel files WITHOUT writing anything
python -m pipeline run --dry-run

# 5. Real run
python -m pipeline run

# 6. Look around
python -m pipeline status
python -m pipeline search "biryani for dinner" -k 3
```

No Gemini key yet? Set `EMBEDDING_PROVIDER=fake` in `.env`. It uses an offline hashing embedder so you can
test everything else. Switch back to `gemini` later and run `python -m pipeline embed --force`.

---------------------------------------------------------------------------------------------------

## 2. Everyday operations (the "future updates" part)

| Situation | What you do |
|---|---|
| New/edited Excel or JSON file | Drop it in `data/incoming/` → `python -m pipeline run` (JSON folder: `--incoming data/json`) |
| Only prices changed | Same command. Only changed rows are updated, only affected documents re-embedded |
| Not sure the file is clean | `python -m pipeline run --dry-run` |
| Items were deleted from Excel | `python -m pipeline run --prune` (default is to keep old rows – safer) |
| Re-do everything | `python -m pipeline run --force` |
| Changed embedding model | Change `.env`, then `python -m pipeline embed --force` |
| Something failed | Read the message (it names the sheet, column and Excel row). Fix, re-run. Nothing partial is ever written |
| What happened last time? | `python -m pipeline status` or `SELECT * FROM etl_runs ORDER BY run_id DESC;` |

**Excel or JSON, your choice.** Both formats go through the same pipeline and give identical results (verified: loading
the JSON files over the Excel-loaded database changed 0 of 1,500+ rows). The JSON layout is
`{"Sheet_Name": [ {"Column": value, ...}, ... ]}`: one file per workbook, one list of row objects per sheet, same headers as Excel.
Use `--incoming data/json` to point at a folder of JSON files (sample files are included). Keep one format per folder: if an .xlsx and a .json
of the same kind sit together, the most recently modified one is used.

Files are recognised by **sheet names**, not file names, so `Menu_Catalog_v2.xlsx` works. If several files of
the same kind are in the folder, the newest wins. Every loaded file is copied to `data/archive/`.

**Scheduling** (when you want it automatic): the command is a normal process with exit code 0/1, so any scheduler can run it.
Linux/Mac cron: `0 2 * * * cd /path/to/project && .venv/bin/python -m pipeline run >> pipeline.log 2>&1`.
Windows: Task Scheduler. Later you can wrap `run_pipeline()` in a Prefect/Airflow task unchanged.

---------------------------------------------------------------------------------------------------

## 3. Project layout

```
docker-compose.yml        Postgres 16 + pgvector
sql/schema.sql            all tables, indexes, views (idempotent)
pipeline/
  config.py               settings + one TableSpec per Excel sheet  <- the "brain"
  extract.py              find files, read Excel/JSON sheets, clean + type-cast
  validate.py             primary key / foreign key / range checks
  load.py                 idempotent upsert (+ optional prune)
  documents.py            DB rows -> natural-language documents
  embeddings.py           Gemini (or fake) embedder, incremental sync, search
  runner.py               orchestration of all steps
  cli.py                  python -m pipeline <command>
data/incoming/            put .xlsx (or .json) files here    data/json/  the JSON versions of the two workbooks
data/archive/             copies of every loaded file
tests/test_pipeline.py    pytest -q   (no DB / API needed)
```

### Database model

```
suppliers ─< ingredients >─ recipes >─ menu_items ─< variants       categories ─< menu_items
branches ─< branch_stock >─ ingredients                menu_items ─< item_aliases, item_addons >─ addons
branches ─< branch_menu >─ menu_items                  deals ─< deal_items >─ menu_items / variants
branches ─< stock_movements                            allergen_guide, service_rules (reference)
documents (text + vector(768))   etl_runs, source_files (pipeline bookkeeping)
```

Views for your chatbot/API: `v_menu_live_availability` (live portions per branch computed from stock × recipes –
verified identical to the Excel snapshot for all 64 recipe-linked branch items) and `v_low_stock`.

---------------------------------------------------------------------------------------------------

## 4. Data Engineering Pipeline – the concepts, mapped to this project

**What is a data pipeline?** An automated, repeatable path that moves data from a *source* to a *destination*
and improves its quality on the way. Here: Excel → clean tables → searchable embeddings.

**ETL vs ELT.** ETL = Extract, Transform, Load (clean *before* loading). ELT = load raw first, transform inside
the warehouse. We use ETL because the data is small and Excel is messy. At scale you would typically land raw
data first and transform with SQL/dbt.

**The stages and where to find them**

| Stage | Purpose | Code |
|---|---|---|
| Ingest / Extract | Find and read the source | `extract.discover`, `read_table` |
| Transform / Clean | Trim, type-cast, blank→NULL, drop duplicated columns | `extract.read_table` |
| Validate (data quality) | Stop bad data from entering | `validate.validate_batch` |
| Load | Write to the destination | `load.upsert` |
| Enrich | Derive embeddings | `documents.py`, `embeddings.py` |
| Orchestrate | Run steps in order, handle failure | `runner.run_pipeline` |
| Observe | Know what happened | `etl_runs`, logs, `status` |

**Key ideas you should be able to explain in an interview**

1. **Idempotency** – running twice gives the same result as once. Achieved with `INSERT … ON CONFLICT DO UPDATE`
   (upsert). Proven in the test run: a forced full reload reported `inserted=0 updated=0` for every table.
2. **Incremental loading** – don't redo unchanged work. Three levels here: (a) file SHA-256 → skip an unchanged
   workbook, (b) `IS DISTINCT FROM` → skip unchanged rows, (c) `content_hash` → skip re-embedding unchanged documents.
3. **Schema & normalisation** – the inventory Excel repeats ingredient names/units on every stock row and stores
   derived per-branch max portions. We store each fact once (names in `ingredients`, portions computed by a view),
   so there's no stale copy to get out of sync.
4. **Primary & foreign keys** – the database refuses orphan rows. We also check them *before* loading to give friendlier
   errors (with Excel row numbers).
5. **Fail fast + atomic loads** – validation errors stop the run before writes; all table writes happen in one
   transaction, so a crash can never leave half a dataset (all-or-nothing, the "A" in ACID).
6. **Data quality dimensions** – completeness (keys not null), uniqueness (no duplicate keys), validity (numbers are numbers,
   prices ≥ 0), consistency (foreign keys). One bug caught during development: pandas turned empty cells into the text `"nan"`;
   the FK check flagged it immediately – validation earns its keep.
7. **Auditability / lineage** – `etl_runs` (who/when/what/result), `source_files` (which file version is loaded),
   `data/archive/` (the exact file), `etl_created_at/etl_updated_at` per row.
8. **Batch vs streaming** – this is batch (run when a file arrives). Streaming (Kafka, CDC) is for continuous, high-volume feeds.
   Your real stock should eventually come from an API/DB feed, not a weekly Excel.
9. **Hybrid retrieval for RAG** – embeddings are great for *meaning* ("something spicy with rice", Roman Urdu aliases)
   but wrong for *fast-changing numbers*. So we embed only stable text and answer "is it in stock?" from
   `v_menu_live_availability` at question time.
10. **Embeddings basics** – text → vector of 768 numbers; similar meaning ⇒ nearby vectors; pgvector's `<=>` is cosine distance;
    an HNSW index makes nearest-neighbour search fast. Documents use task type `RETRIEVAL_DOCUMENT`, questions use `RETRIEVAL_QUERY`.
    Vectors below Gemini's native 3072 dims are re-normalised in code (`_normalize`).
11. **Orchestration** – deciding order, retries and schedules. A tool like Prefect/Airflow adds a scheduler UI and retries; we
    deliberately start without one. The embed step already retries with exponential backoff and commits per batch, so a rate-limit
    failure resumes where it stopped.

---------------------------------------------------------------------------------------------------

## 5. Using it from your chatbot (hybrid query examples)

```sql
-- Semantic: nearest menu documents to a question embedding (:qv = embedding of the user's question)
SELECT doc_id, content, 1 - (embedding <=> :qv::vector) AS score
FROM documents WHERE doc_type = 'menu_item' ORDER BY embedding <=> :qv::vector LIMIT 5;

-- Live facts for the dish the retriever found
SELECT branch_name, price_pkr, availability, live_max_portions
FROM v_menu_live_availability WHERE menu_id = 'MENU001';

-- Restock list
SELECT * FROM v_low_stock ORDER BY branch_name, ingredient_name;
```

Python: `from pipeline.embeddings import get_embedder, search` → `search(conn, get_embedder(), "kids meal", k=5)`.

---------------------------------------------------------------------------------------------------

## 6. Extending it

* **New sheet/table**: add `CREATE TABLE` to `sql/schema.sql`, add a `TableSpec` to `config.SPECS` (parents before children). Done.
* **New document type** (e.g. branches): add a query + loop in `documents.build_documents`.
* **Different embedding model/dimension**: change `.env` **and** `vector(768)` in `schema.sql` (use a fresh table), then `embed --force`.
* **Real stock feed**: the JSON reader is already the hook for this. Have the API/export write the same
  `{"Branch_Stock": [ ... ]}` JSON (or add one small class like `JsonWorkbook` in `extract.py` that fetches it directly).
* **Another file format** (CSV, Google Sheets): add a class with `.sheet_names` and `.parse(sheet)` and return it from `open_workbook`.

## 7. Known limits / honest notes

* Data in the workbooks is synthetic demo data (as the files state). Allergen fields are "Unverified" – do not let a chatbot
  claim an item is allergen-free.
* `Menu_Availability` (inventory) and the `Dashboard`/`START_HERE` sheets are intentionally not loaded: they are derived views or documentation.
* JSON files must keep real `null` for empty values and ISO dates (`2026-10-31`), which your converted files do.
  Error messages for JSON refer to *record numbers* (1 = first row of the sheet) instead of Excel row numbers.
* The Excel `Branch_Stock` values (`On_Hand`, `Available`, …) are read as *cached* values; if a workbook is saved by a tool
  that doesn't compute formulas, those columns arrive empty. Open and re-save in Excel first.
* The Gemini free tier has rate limits; if you see 429 messages the pipeline waits and retries. Re-running continues where it stopped.
* The pipeline was tested end-to-end against PostgreSQL 16 + pgvector with the offline embedder. The Gemini call itself follows the
  official `google-genai` SDK signature but could not be executed in the build environment (no outbound access), so do your first real run
  with `python -m pipeline run` and watch the first batch.
