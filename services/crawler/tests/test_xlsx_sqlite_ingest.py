import io
import json
import sqlite3

import pytest

from app.capture import capture_url
from app.fetch_redirect import FetchResult


def _create_workbook_bytes() -> bytes:
    Workbook = pytest.importorskip("openpyxl").Workbook
    workbook = Workbook()
    sheet1 = workbook.active
    sheet1.title = "Sheet1"
    sheet1["A1"] = "Name"
    sheet1["B1"] = "Value"
    sheet1["A2"] = "Alpha"
    sheet1["B2"] = 123
    sheet2 = workbook.create_sheet("Sheet2")
    sheet2["A1"] = "Item"
    sheet2["A2"] = "Beta"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_xlsx_sqlite_ingest(tmp_path, monkeypatch):
    pytest.importorskip("openpyxl")
    config_path = tmp_path / "crawler.yml"
    ingest_path = tmp_path / "ingest.yml"
    allow_block_path = tmp_path / "allow_block.yml"
    artifact_dir = tmp_path / "artifacts"
    sqlite_path = tmp_path / "structured.db"

    config_path.write_text(
        f"""
user_agent: TestAgent
request_delay: 0
max_depth: 1
timeout: 5
url_canonicalization: {{}}
playwright:
  enabled: false
structured_store:
  enabled: true
  sqlite_path: {sqlite_path.as_posix()}
  xlsx_ingest:
    max_cells: 1000
    batch_size: 10
""".strip(),
        encoding="utf-8",
    )
    ingest_path.write_text("chunking:\n  chunk_size: 20\n  chunk_overlap: 0\n", encoding="utf-8")
    allow_block_path.write_text("allowed_domains:\n  - example.com\n", encoding="utf-8")

    monkeypatch.setattr("app.capture.CONFIG_PATH", config_path)
    monkeypatch.setattr("app.capture.INGEST_CONFIG_PATH", ingest_path)
    monkeypatch.setattr("app.capture.ARTIFACT_DIR", artifact_dir)
    monkeypatch.setattr("app.discovery.CONFIG_PATH", allow_block_path)

    xlsx_bytes = _create_workbook_bytes()
    fetch_result = FetchResult(
        ok=True,
        status="ok",
        status_code=200,
        final_url="https://example.com/workbook.xlsx",
        content_bytes=xlsx_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        redirect_chain=[],
    )
    monkeypatch.setattr(
        "app.capture.fetch_resource_httpx_redirect_safe", lambda *args, **kwargs: fetch_result
    )

    capture_url("https://example.com/workbook.xlsx")

    with sqlite3.connect(sqlite_path) as conn:
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        workbook_count = conn.execute("SELECT COUNT(*) FROM xlsx_workbooks").fetchone()[0]
        sheet_count = conn.execute("SELECT COUNT(*) FROM xlsx_sheets").fetchone()[0]
        cell_count = conn.execute("SELECT COUNT(*) FROM xlsx_cells").fetchone()[0]

    assert doc_count == 1
    assert workbook_count == 1
    assert sheet_count == 2
    assert cell_count > 0

    artifact_dirs = list(artifact_dir.iterdir())
    artifact_json = json.loads((artifact_dirs[0] / "artifact.json").read_text(encoding="utf-8"))
    assert artifact_json["meta"]["xlsx_ingest"]["sheet_count"] == 2
