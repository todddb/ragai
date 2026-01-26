"""
Tests for tools/validate_crawl.py

Tests:
- Correctly counts artifacts
- Detects missing artifact.json
- Detects login/auth text
- Detects duplicate URLs
- Writes latest.json file
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add tools directory to path
TOOLS_DIR = Path(__file__).parent.parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from validate_crawl import (
    Finding,
    validate_artifact,
    compile_patterns,
    DEFAULT_SUSPICIOUS_PATTERNS,
    DEFAULT_BAD_URL_PATTERNS,
    summarize_findings,
)


class TestValidateCrawl(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp()
        self.artifacts_dir = Path(self.tempdir) / "artifacts"
        self.artifacts_dir.mkdir()

        # Create suspicious patterns
        self.suspicious = compile_patterns(DEFAULT_SUSPICIOUS_PATTERNS)
        self.bad_urls = compile_patterns(DEFAULT_BAD_URL_PATTERNS)

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir)

    def _create_artifact(
        self,
        name: str,
        url: str = "https://example.com/page",
        title: str = "Test Page",
        chunks: list = None,
    ) -> Path:
        """Helper to create a test artifact."""
        artifact_dir = self.artifacts_dir / name
        artifact_dir.mkdir()

        # Create artifact.json
        artifact_json = {
            "doc_id": name,
            "url": url,
            "title": title,
            "content_hash": f"hash_{name}",
            "timestamp": "2024-01-01T00:00:00Z",
        }
        (artifact_dir / "artifact.json").write_text(
            json.dumps(artifact_json), encoding="utf-8"
        )

        # Create chunks.jsonl
        if chunks is None:
            chunks = [
                {"chunk_id": f"chunk_0", "chunk_index": 0, "text": "This is a normal page with good content."},
                {"chunk_id": f"chunk_1", "chunk_index": 1, "text": "More content that is relevant and useful."},
            ]

        with (artifact_dir / "chunks.jsonl").open("w", encoding="utf-8") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk) + "\n")

        return artifact_dir

    def test_clean_artifact(self) -> None:
        """Test that a clean artifact has no findings."""
        # Create artifact with enough content to avoid LOW_TOTAL_TEXT (needs > 600 chars)
        artifact_dir = self._create_artifact(
            "clean_artifact",
            chunks=[
                {"chunk_id": "c0", "chunk_index": 0, "text": "This is a page about university policies and regulations that govern student conduct. The policy framework establishes guidelines for academic integrity."},
                {"chunk_id": "c1", "chunk_index": 1, "text": "The policy states that students must comply with all university regulations. This includes following the academic honor code and maintaining proper conduct."},
                {"chunk_id": "c2", "chunk_index": 2, "text": "Additional information about academic integrity follows. Students are expected to submit original work and properly cite all sources used in their assignments."},
                {"chunk_id": "c3", "chunk_index": 3, "text": "Contact the office of student affairs for questions about university policies. Staff members are available during regular business hours to assist students."},
            ],
        )

        findings = validate_artifact(
            artifact_dir,
            self.suspicious,
            self.bad_urls,
            max_chunks=25,
            min_chunk_chars=40,
            repetition_threshold=0.3,
        )

        # Should have no high-severity findings
        high_findings = [f for f in findings if f.severity == "high"]
        self.assertEqual(len(high_findings), 0, f"Clean artifact should have no high findings: {findings}")

    def test_missing_artifact_json(self) -> None:
        """Test detection of missing artifact.json."""
        artifact_dir = self.artifacts_dir / "missing_json"
        artifact_dir.mkdir()

        findings = validate_artifact(
            artifact_dir,
            self.suspicious,
            self.bad_urls,
            max_chunks=25,
            min_chunk_chars=40,
            repetition_threshold=0.3,
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "MISSING_ARTIFACT_JSON")
        self.assertEqual(findings[0].severity, "high")

    def test_missing_chunks(self) -> None:
        """Test detection of missing chunks.jsonl."""
        artifact_dir = self.artifacts_dir / "missing_chunks"
        artifact_dir.mkdir()

        artifact_json = {
            "doc_id": "missing_chunks",
            "url": "https://example.com/",
        }
        (artifact_dir / "artifact.json").write_text(
            json.dumps(artifact_json), encoding="utf-8"
        )

        findings = validate_artifact(
            artifact_dir,
            self.suspicious,
            self.bad_urls,
            max_chunks=25,
            min_chunk_chars=40,
            repetition_threshold=0.3,
        )

        chunk_findings = [f for f in findings if f.code == "MISSING_CHUNKS"]
        self.assertEqual(len(chunk_findings), 1)
        self.assertEqual(chunk_findings[0].severity, "high")

    def test_login_text_detection(self) -> None:
        """Test detection of login/auth text in content."""
        artifact_dir = self._create_artifact(
            "login_page",
            url="https://example.com/protected",
            chunks=[
                {"chunk_id": "c0", "chunk_index": 0, "text": "Please sign in to continue. You must log in to access this content."},
                {"chunk_id": "c1", "chunk_index": 1, "text": "Enter your username and password below."},
            ],
        )

        findings = validate_artifact(
            artifact_dir,
            self.suspicious,
            self.bad_urls,
            max_chunks=25,
            min_chunk_chars=40,
            repetition_threshold=0.3,
        )

        login_findings = [f for f in findings if f.code == "LOGIN_TEXT"]
        self.assertEqual(len(login_findings), 1)
        self.assertEqual(login_findings[0].severity, "high")

    def test_login_path_detection(self) -> None:
        """Test detection of login path in URL."""
        artifact_dir = self._create_artifact(
            "login_path",
            url="https://example.com/secure/login",
            chunks=[
                {"chunk_id": "c0", "chunk_index": 0, "text": "This is a normal looking page content."},
                {"chunk_id": "c1", "chunk_index": 1, "text": "More normal content here."},
            ],
        )

        findings = validate_artifact(
            artifact_dir,
            self.suspicious,
            self.bad_urls,
            max_chunks=25,
            min_chunk_chars=40,
            repetition_threshold=0.3,
        )

        login_findings = [f for f in findings if f.code == "LOGIN_PATH"]
        self.assertEqual(len(login_findings), 1)
        self.assertEqual(login_findings[0].severity, "high")

    def test_low_text_detection(self) -> None:
        """Test detection of very low text content."""
        artifact_dir = self._create_artifact(
            "low_text",
            chunks=[
                {"chunk_id": "c0", "chunk_index": 0, "text": "Hi"},
                {"chunk_id": "c1", "chunk_index": 1, "text": "Bye"},
            ],
        )

        findings = validate_artifact(
            artifact_dir,
            self.suspicious,
            self.bad_urls,
            max_chunks=25,
            min_chunk_chars=40,
            repetition_threshold=0.3,
        )

        low_text_findings = [f for f in findings if f.code == "LOW_TOTAL_TEXT"]
        self.assertEqual(len(low_text_findings), 1)
        self.assertEqual(low_text_findings[0].severity, "high")

    def test_malformed_url_detection(self) -> None:
        """Test detection of malformed URLs."""
        artifact_dir = self._create_artifact(
            "malformed_url",
            url="https://example.com/https:/other.com/page",
            chunks=[
                {"chunk_id": "c0", "chunk_index": 0, "text": "Normal content here with adequate length."},
                {"chunk_id": "c1", "chunk_index": 1, "text": "More content that is relevant."},
            ],
        )

        findings = validate_artifact(
            artifact_dir,
            self.suspicious,
            self.bad_urls,
            max_chunks=25,
            min_chunk_chars=40,
            repetition_threshold=0.3,
        )

        malformed_findings = [f for f in findings if f.code == "MALFORMED_URL"]
        self.assertEqual(len(malformed_findings), 1)
        self.assertEqual(malformed_findings[0].severity, "high")

    def test_summarize_findings(self) -> None:
        """Test summarize_findings function."""
        findings = [
            Finding(severity="high", code="LOGIN_TEXT", message="test", url="https://a.com/"),
            Finding(severity="high", code="LOGIN_TEXT", message="test", url="https://a.com/page"),
            Finding(severity="medium", code="HIGH_REPETITION", message="test"),
            Finding(severity="low", code="BOILERPLATE_HINTS", message="test"),
        ]

        summary = summarize_findings(findings)

        self.assertEqual(summary["counts_by_code"]["LOGIN_TEXT"], 2)
        self.assertEqual(summary["counts_by_code"]["HIGH_REPETITION"], 1)
        self.assertEqual(summary["counts_by_severity"]["high"], 2)
        self.assertEqual(summary["counts_by_severity"]["medium"], 1)
        self.assertEqual(summary["counts_by_severity"]["low"], 1)
        self.assertEqual(summary["top_hosts"]["a.com"], 2)


if __name__ == "__main__":
    unittest.main()
