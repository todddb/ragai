import json

import pytest

from app.capture import capture_url
from app.fetch_redirect import FetchResult


def test_capture_writes_markdown(tmp_path, monkeypatch):
    pytest.importorskip("bs4")
    pytest.importorskip("markdownify")
    config_path = tmp_path / "crawler.yml"
    ingest_path = tmp_path / "ingest.yml"
    allow_block_path = tmp_path / "allow_block.yml"
    artifact_dir = tmp_path / "artifacts"

    config_path.write_text(
        """
user_agent: TestAgent
request_delay: 0
max_depth: 1
timeout: 5
url_canonicalization: {}
playwright:
  enabled: false
structured_store:
  enabled: false
""".strip(),
        encoding="utf-8",
    )
    ingest_path.write_text("chunking:\n  chunk_size: 10\n  chunk_overlap: 0\n", encoding="utf-8")
    allow_block_path.write_text("allowed_domains:\n  - example.com\n", encoding="utf-8")

    monkeypatch.setattr("app.capture.CONFIG_PATH", config_path)
    monkeypatch.setattr("app.capture.INGEST_CONFIG_PATH", ingest_path)
    monkeypatch.setattr("app.capture.ARTIFACT_DIR", artifact_dir)
    monkeypatch.setattr("app.discovery.CONFIG_PATH", allow_block_path)

    html_bytes = b"<html><body><h1>Hello World</h1></body></html>"
    fetch_result = FetchResult(
        ok=True,
        status="ok",
        status_code=200,
        final_url="https://example.com/page",
        content_bytes=html_bytes,
        content_type="text/html",
        redirect_chain=[],
    )

    monkeypatch.setattr(
        "app.capture.fetch_resource_httpx_redirect_safe", lambda *args, **kwargs: fetch_result
    )

    canonical, links = capture_url("https://example.com/page")

    assert canonical == "https://example.com/page"
    assert links == []

    artifact_dirs = list(artifact_dir.iterdir())
    assert artifact_dirs
    content_path = artifact_dirs[0] / "content.md"
    assert content_path.exists()
    assert "Hello World" in content_path.read_text(encoding="utf-8")

    artifact_json = json.loads((artifact_dirs[0] / "artifact.json").read_text(encoding="utf-8"))
    assert artifact_json["status"] == "captured"
    assert artifact_json["markdown_path"] == "content.md"
