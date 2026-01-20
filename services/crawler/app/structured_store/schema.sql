CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    source_url TEXT,
    ingested_at TEXT
);

CREATE TABLE IF NOT EXISTS xlsx_workbooks (
    doc_id TEXT PRIMARY KEY,
    source_url TEXT,
    sheet_count INTEGER,
    truncated INTEGER,
    ingested_at TEXT
);

CREATE TABLE IF NOT EXISTS xlsx_sheets (
    doc_id TEXT,
    sheet_name TEXT,
    sheet_index INTEGER,
    PRIMARY KEY (doc_id, sheet_name)
);

CREATE TABLE IF NOT EXISTS xlsx_cells (
    doc_id TEXT,
    sheet_name TEXT,
    row INTEGER,
    column INTEGER,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_xlsx_cells_doc ON xlsx_cells (doc_id);
CREATE INDEX IF NOT EXISTS idx_xlsx_cells_sheet ON xlsx_cells (doc_id, sheet_name);
