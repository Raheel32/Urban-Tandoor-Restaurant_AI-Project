-- =====================================================================
-- Urban Tandoor | PostgreSQL + pgvector schema
-- Idempotent: safe to run many times (CREATE ... IF NOT EXISTS).
-- Every business table carries two audit columns:
--   etl_created_at  when the row first arrived
--   etl_updated_at  when the row last actually changed
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------
-- 1. Reference / dimension tables
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id      TEXT PRIMARY KEY,
    supplier_name    TEXT NOT NULL,
    supply_category  TEXT,
    contact          TEXT,
    verification     TEXT,
    etl_created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS branches (
    branch_id        TEXT PRIMARY KEY,
    branch_name      TEXT NOT NULL,
    address_demo     TEXT,
    tables           INT,
    seats            INT,
    hours            TEXT,
    status           TEXT,
    etl_created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingredients (
    ingredient_id    TEXT PRIMARY KEY,
    ingredient_name  TEXT NOT NULL,
    category         TEXT,
    unit             TEXT,
    unit_cost_pkr    NUMERIC(12,2) CHECK (unit_cost_pkr >= 0),
    default_reorder  NUMERIC(14,4),
    default_target   NUMERIC(14,4),
    supplier_id      TEXT REFERENCES suppliers(supplier_id),
    status           TEXT,
    etl_created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS categories (
    category_id           TEXT PRIMARY KEY,
    category_name         TEXT NOT NULL,
    sort_order            INT,
    status                TEXT,
    customer_description  TEXT,
    etl_created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS menu_items (
    menu_id            TEXT PRIMARY KEY,
    item_name          TEXT NOT NULL,
    category_id        TEXT REFERENCES categories(category_id),
    category           TEXT,
    description        TEXT,
    base_price_pkr     NUMERIC(12,2) CHECK (base_price_pkr >= 0),
    currency           TEXT,
    item_type          TEXT,
    catalog_status     TEXT,
    recipe_linked      TEXT,
    prep_time_min      INT,
    dietary_hint       TEXT,
    allergen_hint      TEXT,
    allergen_verified  TEXT,
    service_window     TEXT,
    upsell_eligible    TEXT,
    slug               TEXT,
    data_provenance    TEXT,
    etl_created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS variants (
    variant_id             TEXT PRIMARY KEY,
    menu_id                TEXT NOT NULL REFERENCES menu_items(menu_id),
    variant_name           TEXT,
    price_pkr              NUMERIC(12,2) CHECK (price_pkr >= 0),
    recipe_multiplier_est  NUMERIC(8,3),
    shown_in_menu          TEXT,
    approved_for_orders    TEXT,
    operational_notes      TEXT,
    etl_created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS addons (
    addon_id              TEXT PRIMARY KEY,
    addon_name            TEXT NOT NULL,
    price_pkr             NUMERIC(12,2) CHECK (price_pkr >= 0),
    category              TEXT,
    allergen_hint         TEXT,
    ingredient_id         TEXT REFERENCES ingredients(ingredient_id),
    qty_ingredient_units  NUMERIC(14,4),
    shown_in_menu         TEXT,
    order_status          TEXT,
    etl_created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS item_addons (
    mapping_id         TEXT PRIMARY KEY,
    menu_id            TEXT NOT NULL REFERENCES menu_items(menu_id),
    addon_id           TEXT NOT NULL REFERENCES addons(addon_id),
    selectable         TEXT,
    operational_notes  TEXT,
    etl_created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS branch_menu (
    branch_menu_id          TEXT PRIMARY KEY,
    branch_id               TEXT NOT NULL REFERENCES branches(branch_id),
    branch_name             TEXT,
    menu_id                 TEXT NOT NULL REFERENCES menu_items(menu_id),
    enabled_for_order       TEXT,
    price_pkr               NUMERIC(12,2) CHECK (price_pkr >= 0),
    base_variant_orderable  TEXT,
    stock_decision          TEXT,
    snapshot_max_portions   NUMERIC(14,2),
    notes                   TEXT,
    etl_created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inventory_links (
    menu_id                TEXT NOT NULL REFERENCES menu_items(menu_id),
    branch_id              TEXT NOT NULL REFERENCES branches(branch_id),
    recipe_linked          TEXT,
    snapshot_max_portions  NUMERIC(14,2),
    recipe_link_status     TEXT,
    runtime_check_policy   TEXT,
    etl_created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (menu_id, branch_id)
);

CREATE TABLE IF NOT EXISTS item_aliases (
    alias_id         TEXT PRIMARY KEY,
    menu_id          TEXT NOT NULL REFERENCES menu_items(menu_id),
    customer_phrase  TEXT NOT NULL,
    language         TEXT,
    alias_notes      TEXT,
    etl_created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deals (
    deal_id              TEXT PRIMARY KEY,
    deal_name            TEXT NOT NULL,
    deal_type            TEXT,
    description          TEXT,
    deal_price_pkr       NUMERIC(12,2) CHECK (deal_price_pkr >= 0),
    currency             TEXT,
    approval_status      TEXT,
    branches             TEXT,
    service_window       TEXT,
    min_spend_pkr        NUMERIC(12,2),
    stack_bank_discount  TEXT,
    policy_notes         TEXT,
    etl_created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deal_items (
    deal_id         TEXT NOT NULL REFERENCES deals(deal_id),
    menu_id         TEXT NOT NULL REFERENCES menu_items(menu_id),
    item_name       TEXT,
    qty             INT,
    variant_id      TEXT NOT NULL REFERENCES variants(variant_id),
    etl_created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (deal_id, menu_id, variant_id)
);

CREATE TABLE IF NOT EXISTS allergen_guide (
    allergen_code   TEXT PRIMARY KEY,
    display_name    TEXT,
    status          TEXT,
    use_policy      TEXT,
    etl_created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS service_rules (
    rule_id              TEXT PRIMARY KEY,
    rule_name            TEXT NOT NULL,
    value                TEXT,
    applies_to           TEXT,
    verification_status  TEXT,
    comment              TEXT,
    etl_created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 2. Inventory facts (change often)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS branch_stock (
    branch_id        TEXT NOT NULL REFERENCES branches(branch_id),
    ingredient_id    TEXT NOT NULL REFERENCES ingredients(ingredient_id),
    opening          NUMERIC(14,4),
    received         NUMERIC(14,4),
    consumed         NUMERIC(14,4),
    wastage          NUMERIC(14,4),
    adjustment       NUMERIC(14,4),
    reserved         NUMERIC(14,4),
    on_hand          NUMERIC(14,4),
    available        NUMERIC(14,4),
    reorder_level    NUMERIC(14,4),
    target_stock     NUMERIC(14,4),
    suggested_buy    NUMERIC(14,4),
    stock_status     TEXT,
    unit_cost_pkr    NUMERIC(12,2),
    stock_value_pkr  NUMERIC(16,2),
    updated_at       DATE,
    etl_created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (branch_id, ingredient_id)
);

CREATE TABLE IF NOT EXISTS recipes (
    menu_id          TEXT NOT NULL REFERENCES menu_items(menu_id),
    ingredient_id    TEXT NOT NULL REFERENCES ingredients(ingredient_id),
    qty_per_portion  NUMERIC(14,4) CHECK (qty_per_portion >= 0),
    etl_created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (menu_id, ingredient_id)
);

CREATE TABLE IF NOT EXISTS stock_movements (
    movement_id     TEXT PRIMARY KEY,
    movement_date   DATE,
    branch_id       TEXT NOT NULL REFERENCES branches(branch_id),
    ingredient_id   TEXT NOT NULL REFERENCES ingredients(ingredient_id),
    movement_type   TEXT,
    qty_signed      NUMERIC(14,4),
    unit            TEXT,
    reference       TEXT,
    reason          TEXT,
    data_type       TEXT,
    etl_created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 3. Document embeddings (for RAG / semantic search)
--    One row per "document" (menu item, deal, rule, allergen, ingredient).
--    content_hash lets the pipeline skip re-embedding unchanged documents.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    doc_id           TEXT PRIMARY KEY,           -- e.g. menu_item:MENU001
    doc_type         TEXT NOT NULL,
    source_table     TEXT NOT NULL,
    source_id        TEXT NOT NULL,
    content          TEXT NOT NULL,
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_hash     TEXT NOT NULL,
    embedding        vector(768) NOT NULL,       -- must match EMBEDDING_DIM
    embedding_model  TEXT NOT NULL,
    etl_created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    etl_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents (doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_embedding
    ON documents USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------
-- 4. Pipeline bookkeeping (observability + incremental loading)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl_runs (
    run_id       BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running',   -- running | success | failed
    trigger      TEXT,
    files        JSONB,
    stats        JSONB,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS source_files (
    workbook   TEXT PRIMARY KEY,       -- 'menu' | 'inventory'
    file_name  TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    loaded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 5. Serving views (what the chatbot / API should query)
-- ---------------------------------------------------------------------
-- Live portions per branch, computed from current stock + recipes
-- (instead of trusting the Excel "Snapshot_Max_Portions" column).
CREATE OR REPLACE VIEW v_menu_live_availability AS
SELECT bm.branch_id,
       bm.branch_name,
       bm.menu_id,
       mi.item_name,
       bm.price_pkr,
       bm.enabled_for_order,
       bm.snapshot_max_portions,
       r.live_max_portions,
       CASE
           WHEN bm.enabled_for_order <> 'Yes'      THEN 'DISABLED'
           WHEN r.live_max_portions IS NULL        THEN 'NO_RECIPE'
           WHEN r.live_max_portions <= 0           THEN 'OUT_OF_STOCK'
           WHEN r.live_max_portions < 10           THEN 'LOW'
           ELSE 'AVAILABLE'
       END AS availability
FROM branch_menu bm
JOIN menu_items mi ON mi.menu_id = bm.menu_id
LEFT JOIN (
    SELECT bs.branch_id,
           rc.menu_id,
           MIN(FLOOR(bs.available / NULLIF(rc.qty_per_portion, 0)))::int AS live_max_portions
    FROM recipes rc
    JOIN branch_stock bs ON bs.ingredient_id = rc.ingredient_id
    GROUP BY bs.branch_id, rc.menu_id
) r ON r.branch_id = bm.branch_id AND r.menu_id = bm.menu_id;

CREATE OR REPLACE VIEW v_low_stock AS
SELECT bs.branch_id, b.branch_name, bs.ingredient_id, i.ingredient_name, i.unit,
       bs.available, bs.reorder_level, bs.suggested_buy, bs.stock_status,
       s.supplier_name
FROM branch_stock bs
JOIN branches b     ON b.branch_id = bs.branch_id
JOIN ingredients i  ON i.ingredient_id = bs.ingredient_id
LEFT JOIN suppliers s ON s.supplier_id = i.supplier_id
WHERE bs.stock_status IS DISTINCT FROM 'OK';
