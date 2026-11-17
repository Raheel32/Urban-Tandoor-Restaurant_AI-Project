![Urban Tandoor & Grill AI Restaurant Assistant](assets/urban-tandoor-ai-dining-experience.png)
# Urban Tandoor â€“ MongoDB + Embeddings + FastAPI

Loads the two JSON files (menu catalog + two-branch inventory) into **MongoDB Atlas** as embedded
documents, creates **Gemini embeddings** for semantic search, and exposes **CRUD for every collection**
through **FastAPI**.

```
 data/json/*.json â”€â–º TRANSFORM â”€â–º VALIDATE â”€â–º UPSERT â”€â–º INDEXES â”€â–º EMBED â”€â–º MongoDB Atlas
                     embed lists   Pydantic +  only changed   (also vector   only changed       â”‚
                     into documents references  documents     search index)  documents          â–¼
                                                                                         FastAPI  (/docs)
                                                                      CRUD â—„â”€â”€ GET/POST/PUT/PATCH/DELETE
                                                                      GET /search Â· GET /availability/{branch}
```

---------------------------------------------------------------------------------------------------

## 1. Setup (about 15 minutes, no Docker needed)

### 1.1 Create the free Atlas database
1. Sign up at **mongodb.com/cloud/atlas** â†’ **Create** a free **M0** cluster (any region near you).
2. **Database Access** â†’ *Add New Database User* â†’ username + password (write the password down).
3. **Network Access** â†’ *Add IP Address* â†’ **Add Current IP Address** (or `0.0.0.0/0` for a demo; not for real data).
4. **Clusters â†’ Connect â†’ Drivers** â†’ copy the connection string:
   `mongodb+srv://USER:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority`

### 1.2 Install and configure
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
Edit `.env`:
* `MONGODB_URI` â†’ your string with the real password. Special characters must be URL-encoded (`@` â†’ `%40`, `#` â†’ `%23`).
* `GEMINI_API_KEY` â†’ free key from aistudio.google.com/apikey (or set `EMBEDDING_PROVIDER=fake` to test without it).

### 1.3 Load the data, then start the API
```powershell
python -m app.ingest --dry-run        # validate the JSON only, write nothing
python -m app.ingest                  # load everything + embeddings + Atlas vector index
uvicorn app.main:app --reload         # API on http://127.0.0.1:8000
```
Open **http://127.0.0.1:8000/docs**. Every endpoint can be tried from the browser (button *Try it out*).

---------------------------------------------------------------------------------------------------

## 2. What is stored (10 collections + 2 system collections)

| Collection | Documents | Notes |
|---|---:|---|
| `menu_items` | 118 | **Embedded:** `variants[]`, `addons[]`, `aliases[]`, `branches[]` (price/availability per branch), `recipe[]` |
| `categories` | 8 | |
| `deals` | 6 | **Embedded:** `items[]` |
| `ingredients` | 60 | references `supplier_id` |
| `suppliers` | 7 | |
| `branches` | 2 | |
| `branch_stock` | 120 | `_id` = `BRANCH_ID:INGREDIENT_ID` |
| `stock_movements` | 8 | |
| `allergen_guide`, `service_rules` | 8, 7 | |
| `documents` | 199 | text + 768-number embedding for semantic search |
| `ingest_runs` | â€“ | one row per ingest: when, result, stats |

Not loaded: `START_HERE`, `Dashboard`, `Menu_Availability` (documentation / derived values). Live availability is computed from stock instead.

One menu document (shortened, values illustrative):
```json
{ "_id": "MENU001", "menu_id": "MENU001", "item_name": "Chicken Biryani", "base_price_pkr": 420.0,
  "variants": [ {"variant_id": "...", "variant_name": "...", "price_pkr": 420.0} ],
  "addons":   [ {"addon_id": "...", "addon_name": "...", "price_pkr": 100.0, "selectable": "Yes"} ],
  "aliases":  [ {"alias_id": "...", "customer_phrase": "chicken biryani", "language": "English"} ],
  "branches": [ {"branch_id": "UTG-KHI-001", "enabled_for_order": "Yes", "price_pkr": 420.0} ],
  "recipe":   [ {"ingredient_id": "ING001", "ingredient_name": "...", "qty_per_portion": 0.2} ] }
```

---------------------------------------------------------------------------------------------------

## 3. Using the API

| Method & path | Purpose |
|---|---|
| `POST /menu-items` | create (409 if the id exists, 422 if the data is invalid) |
| `GET /menu-items?skip=0&limit=50&q=biryani&branch_id=UTG-KHI-001` | list, paginate, filter, search |
| `GET /menu-items/{id}` | read one (404 if missing) |
| `PUT /menu-items/{id}` | replace the whole document |
| `PATCH /menu-items/{id}` | change only the fields you send; `null` removes a field; lists are replaced whole |
| `DELETE /menu-items/{id}` | delete (409 if something still references it) |
| `GET /search?q=spicy chicken rice&k=5&doc_type=menu_item` | search by meaning |
| `GET /availability/{branch_id}` | live portions per dish from current stock |
| `POST /admin/reindex` | re-embed changed documents now (`?force=true` re-embeds all) |
| `GET /health` | API + database check |

The same six endpoints exist for: `/menu-items`, `/categories`, `/deals`, `/ingredients`, `/suppliers`,
`/branches`, `/branch-stock`, `/stock-movements`, `/allergens`, `/service-rules`.

PowerShell examples:
```powershell
Invoke-RestMethod http://127.0.0.1:8000/menu-items/MENU001

Invoke-RestMethod -Method Patch -Uri http://127.0.0.1:8000/menu-items/MENU001 `
  -ContentType "application/json" -Body '{"base_price_pkr": 450}'

Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8000/branch-stock/UTG-KHI-001:ING001" `
  -ContentType "application/json" -Body '{"available": 12.5, "stock_status": "REORDER"}'

Invoke-RestMethod "http://127.0.0.1:8000/search?q=something spicy with rice&k=3"
```

**Embeddings stay fresh automatically.** After a create/update/delete on menu items, deals, ingredients, allergens or
rules, the changed documents are re-embedded in the background (only those whose text changed).
Turn that off with `AUTO_REINDEX=false` in `.env` and use `POST /admin/reindex` instead.

---------------------------------------------------------------------------------------------------

## 4. Updating data later

Replace the JSON files in `data/json/` (names can change; files are recognised by their sheet names) and run
`python -m app.ingest` again. It is **idempotent**: unchanged documents are skipped, so the second run says
`inserted=0 updated=0` everywhere and embeds nothing.

| Option | Meaning |
|---|---|
| `--dry-run` | validate only |
| `--prune` | also delete documents that are no longer in the files (**also deletes documents you created through the API**) |
| `--force` | re-embed everything |
| `--embed-only` | skip loading; only rebuild embeddings / vector index |
| `--json-dir PATH` | read the files from another folder |

Caution: the JSON files are the source of truth *for ingestion*. If you edited a document through the API and then
ingest again, the file's version wins for documents that exist in the file.

---------------------------------------------------------------------------------------------------

## 5. MongoDB concepts, mapped to this project

* **Document database.** A document is JSON-like (stored as BSON) and can hold lists and sub-documents. A collection is a
  set of documents, similar to a table but without a fixed schema.
* **`_id`.** Every document has a unique `_id`, always indexed. We use the business key (`MENU001`, or `BRANCH:ING` for stock) so
  loading the same data twice updates the same document instead of creating a duplicate (idempotency).
* **Embed vs reference** (the main design decision in MongoDB):
  * *Embed* data that is read together and belongs to one parent: a dish and its sizes, add-ons, aliases, branch prices and
    recipe. One read returns everything, no joins.
  * *Reference* (store an id) data with its own life or shared by many: ingredients, suppliers, branches, and stock levels that change daily.
  * Trade-off: embedded copies can go stale (the recipe stores `ingredient_name`; an ingest refreshes it) and an embedded add-on price
    appears in every dish that offers it. We accepted this because the catalog changes rarely and is read constantly.
* **No foreign keys.** MongoDB will happily store an orphan. So `app/transform.py` checks every reference before writing, and the
  API refuses to delete a branch/ingredient/supplier/category that is still in use (409).
* **Schema discipline.** MongoDB is schema-less; `app/schemas.py` (Pydantic) is the schema. The same models validate the JSON at
  ingest time and every API request, so wrong types, negative prices and misspelled fields are rejected.
* **Indexes** (`app/indexes.py`) make the filters fast: category, branch, alias phrase, branch+stock status, and so on.
* **Why PyMongo (sync) instead of async Motor?** Plain `def` endpoints are easier to read and debug for a first API; FastAPI runs
  them in a thread pool, so requests do not block each other. Motor is deprecated in favour of PyMongo's own async client, which is
  an easy upgrade later.
* **Dates** are ISO strings (`2026-10-31`): sortable, JSON friendly, no time-zone surprises for date-only data.

### Embeddings and vector search
* An embedding turns text into a vector of 768 numbers; texts with similar meaning have vectors that point the same way
  (cosine similarity). Documents are embedded with task type `RETRIEVAL_DOCUMENT`, questions with `RETRIEVAL_QUERY`.
* Only **stable text** is embedded (names, descriptions, prices, aliases, rules). **Live stock is not**: it changes hourly, so
  `/availability` computes it from `branch_stock` at question time. Meaning from embeddings, facts from the database
  = *hybrid retrieval*, the standard design for RAG chatbots.
* Search engines: **Atlas `$vectorSearch`** (approximate nearest neighbour over an HNSW index, scales to millions) and an
  **in-Python exact search** used automatically while the Atlas index is building or if it is unavailable. For ~200 documents both
  answer in milliseconds. `GET /search` shows which engine answered (`"engine"`).
* The vector index is created by `python -m app.ingest`. Atlas needs about a minute to build it. If automatic creation is refused,
  create it in the Atlas UI: *Atlas Search â†’ Create Search Index â†’ JSON editor â†’ collection `documents` â†’ index name
  `documents_vector_index`, type **Vector Search*** and paste:
  ```json
  { "fields": [
      { "type": "vector", "path": "embedding", "numDimensions": 768, "similarity": "cosine" },
      { "type": "filter", "path": "doc_type" } ] }
  ```

---------------------------------------------------------------------------------------------------

## 6. Project layout
```
app/
  config.py        settings from .env
  db.py            MongoDB connection + FastAPI dependency
  schemas.py       Pydantic models + collection registry          <- add new fields here
  transform.py     JSON sheets -> embedded documents, reference checks
  ingest.py        python -m app.ingest   (idempotent upserts)
  documents.py     stored documents -> text for embedding
  embeddings.py    Gemini / fake embedder, incremental sync, background re-index
  search.py        Atlas $vectorSearch + Python fallback
  indexes.py       regular indexes + Atlas vector index
  routers/crud.py  one factory = CRUD for all 10 collections     <- add a CrudConfig to add a collection
  routers/extras.py  /search  /availability  /admin/reindex  /health
  main.py          FastAPI app
data/json/         the two JSON files
tests/             pytest -q  (34 tests, in-memory MongoDB, no internet needed)
```
**Adding a collection later:** add a model + `Coll(...)` in `schemas.py`, build its documents in `transform.py`, and add one
`CrudConfig` in `routers/crud.py`.

## 7. Tests
```powershell
pytest -q
```
They run against an in-memory MongoDB (mongomock) and the offline embedder, using your real JSON. They check ingestion,
idempotency, bad-data rejection, every CRUD path, filters, the delete guard, live availability against the original
snapshot values, search, and background re-embedding.

## 8. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `ServerSelectionTimeoutError` / `/health` fails | Your IP is not allowed in Atlas *Network Access*, or the URI is wrong |
| `bad auth` / `Authentication failed` | Wrong user or password; URL-encode special characters in the password |
| `The DNS query name does not exist` / SRV error | Copy the `mongodb+srv://` string again; check VPN/DNS; make sure `dnspython` is installed |
| `GEMINI_API_KEY is not set` | Fill `.env`, or use `EMBEDDING_PROVIDER=fake` while testing |
| `/search` says `"engine": "python"` | Normal while the Atlas index builds (about a minute) or if it could not be created automatically |
| `N data problem(s) found; nothing was written` | Read the listed lines: they name the collection, id and field |
| Gemini 429 errors | Free-tier rate limit; the pipeline waits and retries, and progress is saved per batch, so just run it again |

## 9. Honest notes
* Built and tested end to end with an in-memory MongoDB and the offline embedder, plus a live HTTP smoke test. **Not executed against real
  Atlas or the Gemini API** (no outbound access where it was built). The code follows the official PyMongo and google-genai APIs; do the first
  real run with `python -m app.ingest` and read the log. Atlas vector-index creation is the part most likely to need the UI fallback above.
* Allergen fields in your data are marked unverified; do not let a chatbot claim a dish is allergen-free.
* Never commit `.env` (it is in `.gitignore`). If a password or key ever lands on GitHub, rotate it immediately.
