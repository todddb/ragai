#!/usr/bin/env python3
"""
tools/validate_crawl.py

Validate crawl artifacts and log results as a first-class job.

Features:
 - timezone-aware timestamps
 - evidence snippet for LOGIN_TEXT (first_match_snippet)
 - tightened suspicious patterns (fewer false positives)
 - MALFORMED_URL detection with evidence (original malformed url)
 - DUPLICATE_URL detection across artifacts
 - safer logging / permission-handling when writing summary files
 - summarize / rollups (counts by code, top hosts)
 - --quarantine option: move flagged artifacts to data/quarantine
 - --limit option: limit number of artifacts to validate (0 = all)
 - --since option: only validate artifacts modified after timestamp
 - --json-out option: write summary also to specified path
 - Writes validate_crawl_latest.json for API consumption
 - default locations: jobs -> data/logs/jobs, summary -> data/logs/summaries
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import uuid
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from collections import Counter
from urllib.parse import urlparse, urljoin

# -----------------------------
# Config / heuristics
# -----------------------------

DEFAULT_SUSPICIOUS_PATTERNS = [
    # keep explicit login / auth flows and path-based hits; avoid generic words that cause false positives
    r"cas/login",
    r"/cas\b",
    r"shibboleth",
    r"\b(saml|oauth|openid)\b",
    r"please\s+sign\s+in",
    r"please\s+sign\s+in\s+to\s+continue",
    r"you\s+must\s+(log\s*in|sign\s*in)\b",
    r"redirecting\s+to\s+.*login",
    r"(/secure/)|(/login\b)|(/signin\b)",
]

DEFAULT_BAD_URL_PATTERNS = [
    r"https?://[^/]+/https?:/[^/]",
    r"https?://[^/]+/https?:[^/]",
    r"https?:/[^/]",
    r"http?:/[^/]",
]

DEFAULT_BOILERPLATE_HINTS = [
    "skip to main content",
    "privacy policy",
    "terms of use",
    "copyright",
    "all rights reserved",
]

SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    doc_id: Optional[str] = None
    url: Optional[str] = None
    artifact_dir: Optional[str] = None
    evidence: Optional[str] = None


# -----------------------------
# Helpers
# -----------------------------


def now_utc() -> str:
    # timezone-aware UTC timestamp with Z
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_jsonl_texts(path: Path, max_lines: int) -> Iterable[str]:
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= max_lines:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                yield ""
                continue
            text = obj.get("text") or obj.get("content") or ""
            yield text if isinstance(text, str) else ""


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def compile_patterns(pats: List[str]) -> List[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in pats]


def score_repetition(text: str) -> float:
    lines = [normalize_ws(l) for l in text.splitlines() if normalize_ws(l)]
    if len(lines) < 10:
        return 0.0
    freq: Dict[str, int] = {}
    for l in lines:
        freq[l] = freq.get(l, 0) + 1
    return max(freq.values()) / len(lines)


def severity_at_least(sev: str, threshold: str) -> bool:
    return SEVERITY_ORDER.get(sev, 0) >= SEVERITY_ORDER.get(threshold, 999)


def first_match_snippet(text: str, pat: re.Pattern, ctx: int = 80) -> str:
    m = pat.search(text)
    if not m:
        return ""
    start = max(0, m.start() - ctx)
    end = min(len(text), m.end() + ctx)
    return text[start:end].replace("\n", " ")


def summarize_findings(findings: List[Finding]) -> Dict[str, Any]:
    codes = Counter(f.code for f in findings)
    severities = Counter(f.severity for f in findings)
    hosts = Counter()
    for f in findings:
        if f.url:
            try:
                hosts[urlparse(f.url).netloc.lower()] += 1
            except Exception:
                hosts["(bad_url_parse)"] += 1
    return {
        "counts_by_code": dict(codes.most_common(50)),
        "counts_by_severity": dict(severities),
        "top_hosts": dict(hosts.most_common(50)),
    }


def sanitize_malformed_url(url: str) -> Optional[str]:
    """
    If url looks like: https://host/https:/otherhost/... extract inner absolute URL.
    Return the extracted URL or None if nothing to salvage.
    """
    if not url:
        return None
    m = re.search(r"https?://[^/]+/(https?://.*)", url)
    if m:
        return m.group(1)
    return None


def try_move_with_sudo(src: str, dst_dir: str) -> bool:
    """
    Attempt to move src into dst_dir. If a PermissionError occurs, call sudo mv.
    Return True on success.
    """
    try:
        dst = os.path.join(dst_dir, os.path.basename(src))
        os.makedirs(dst_dir, exist_ok=True)
        os.rename(src, dst)
        return True
    except PermissionError:
        # fall back to sudo
        try:
            subprocess.check_call(["sudo", "mv", src, dst_dir])
            return True
        except subprocess.CalledProcessError:
            return False
    except Exception:
        return False


# -----------------------------
# Validation logic
# -----------------------------


def validate_artifact(
    artifact_dir: Path,
    suspicious: List[re.Pattern],
    bad_urls: List[re.Pattern],
    max_chunks: int,
    min_chunk_chars: int,
    repetition_threshold: float,
) -> List[Finding]:

    findings: List[Finding] = []
    artifact_json = artifact_dir / "artifact.json"
    chunks_jsonl = artifact_dir / "chunks.jsonl"

    if not artifact_json.exists():
        return [
            Finding(
                severity="high",
                code="MISSING_ARTIFACT_JSON",
                message="artifact.json missing",
                artifact_dir=str(artifact_dir),
            )
        ]

    meta = load_json(artifact_json)
    doc_id = meta.get("doc_id") or meta.get("id") or artifact_dir.name
    url = meta.get("url") or meta.get("source_url") or ""

    # URL checks
    if not url:
        findings.append(
            Finding(
                severity="high",
                code="MISSING_URL",
                message="artifact missing url",
                doc_id=doc_id,
                artifact_dir=str(artifact_dir),
            )
        )
    else:
        for p in bad_urls:
            if p.search(url):
                findings.append(
                    Finding(
                        severity="high",
                        code="MALFORMED_URL",
                        message="URL appears malformed (bad join)",
                        doc_id=doc_id,
                        url=url,
                        artifact_dir=str(artifact_dir),
                        evidence=url,
                    )
                )
                # try to salvage the inner url as evidence (not modifying artifact.json here)
                salv = sanitize_malformed_url(url)
                if salv:
                    findings[-1].evidence = salv
                break

    if not chunks_jsonl.exists():
        findings.append(
            Finding(
                severity="high",
                code="MISSING_CHUNKS",
                message="chunks.jsonl missing",
                doc_id=doc_id,
                url=url,
                artifact_dir=str(artifact_dir),
            )
        )
        return findings

    texts = list(iter_jsonl_texts(chunks_jsonl, max_chunks))
    combined = "\n".join(texts)
    combined_norm = normalize_ws(combined)

    # Tiny chunks / weak extraction
    tiny = sum(1 for t in texts if len(t.strip()) < min_chunk_chars)
    total_chars = sum(len(t.strip()) for t in texts)

    if total_chars < 600:
        findings.append(
            Finding(
                severity="high",
                code="LOW_TOTAL_TEXT",
                message=f"total extracted text is very small ({total_chars} chars across {len(texts)} chunks)",
                doc_id=doc_id,
                url=url,
                artifact_dir=str(artifact_dir),
            )
        )
    elif tiny >= max(6, int(len(texts) * 0.85)):
        findings.append(
            Finding(
                severity="medium",
                code="MOSTLY_TINY_CHUNKS",
                message=f"{tiny}/{len(texts)} chunks under {min_chunk_chars} chars (total={total_chars})",
                doc_id=doc_id,
                url=url,
                artifact_dir=str(artifact_dir),
            )
        )

    # Login / auth text detection (use stricter patterns)
    for p in suspicious:
        m = p.search(combined_norm)
        if m:
            findings.append(
                Finding(
                    severity="high",
                    code="LOGIN_TEXT",
                    message="login/auth text detected in content",
                    doc_id=doc_id,
                    url=url,
                    artifact_dir=str(artifact_dir),
                    evidence=first_match_snippet(combined_norm, p),
                )
            )
            break

    # If url path looks authenticator-ish, raise a finding even if body didn't match strongly
    try:
        if url and re.search(r"(/secure/)|(/login\b)|(/signin\b)", url, re.I):
            findings.append(
                Finding(
                    severity="high",
                    code="LOGIN_PATH",
                    message="URL path indicates secure/login area",
                    doc_id=doc_id,
                    url=url,
                    artifact_dir=str(artifact_dir),
                )
            )
    except Exception:
        pass

    # Boilerplate hints (low severity)
    hints = [h for h in DEFAULT_BOILERPLATE_HINTS if h in combined_norm]
    if hints:
        findings.append(
            Finding(
                severity="low",
                code="BOILERPLATE_HINTS",
                message=f"boilerplate hints found: {', '.join(hints[:3])}",
                doc_id=doc_id,
                url=url,
                artifact_dir=str(artifact_dir),
            )
        )

    # Repetition
    rep = score_repetition(combined)
    if rep >= repetition_threshold:
        findings.append(
            Finding(
                severity="medium",
                code="HIGH_REPETITION",
                message=f"repetition ratio {rep:.2f}",
                doc_id=doc_id,
                url=url,
                artifact_dir=str(artifact_dir),
            )
        )

    return findings


# -----------------------------
# Main
# -----------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate crawl artifacts for quality issues")
    ap.add_argument("--artifacts-dir", default="data/artifacts", help="Directory containing artifacts")
    ap.add_argument("--quarantine-dir", default="data/quarantine", help="Directory for quarantined artifacts")
    ap.add_argument("--output-dir", default="data/logs/summaries", help="Directory for summary JSON files")
    ap.add_argument("--limit", type=int, default=0, help="Max artifacts to validate (0 = no limit)")
    ap.add_argument("--sample", type=int, default=0, help="Random sample size (0 = no sampling, use --all or --limit)")
    ap.add_argument("--all", action="store_true", help="Validate all artifacts (default behavior when limit=0 and sample=0)")
    ap.add_argument("--since", help="Only validate artifacts modified after this ISO timestamp")
    ap.add_argument("--seed", type=int, help="Random seed for reproducible sampling")
    ap.add_argument("--max-chunks", type=int, default=25, help="Max chunks to read per artifact")
    ap.add_argument("--min-chunk-chars", type=int, default=40, help="Min chars for a chunk to not be 'tiny'")
    ap.add_argument("--min-text-threshold", type=int, default=300, help="Min total text chars (LOW_TOTAL_TEXT threshold)")
    ap.add_argument("--repetition-threshold", type=float, default=0.30, help="Repetition ratio threshold")
    ap.add_argument("--fail-on", choices=["low", "medium", "high"], default="high", help="Exit code 1 if findings at this severity")
    ap.add_argument("--quarantine", action="store_true", help="Move flagged artifacts to quarantine directory")
    ap.add_argument("--json-out", help="Additional path to write summary JSON")
    ap.add_argument("--verbose", action="store_true", help="Print more details")
    args = ap.parse_args()

    job_id = f"validate_crawl_{uuid.uuid4()}"
    started = now_utc()

    # Log dirs
    jobs_dir = Path("data/logs/jobs")
    summary_dir = Path(args.output_dir)
    quarantine_dir = Path(args.quarantine_dir)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)

    job_log_path = jobs_dir / f"{job_id}.log"
    summary_path = summary_dir / f"{job_id}.json"
    latest_path = summary_dir / "validate_crawl_latest.json"

    def log(msg: str):
        line = f"[{now_utc()}] {msg}"
        print(line)
        try:
            with job_log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except PermissionError:
            # best-effort: attempt sudo tee append
            try:
                subprocess.run(["sudo", "tee", "-a", str(job_log_path)], input=(line + "\n").encode("utf-8"), check=False)
            except Exception:
                pass

    log(f"Job started ({job_id})")
    log(f"Artifacts dir: {args.artifacts_dir}")
    log(f"Quarantine dir: {args.quarantine_dir}")

    artifact_root = Path(args.artifacts_dir)

    # Collect all artifact directories with metadata
    artifact_info = []
    since_dt = None
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        except ValueError:
            log(f"Warning: could not parse --since timestamp '{args.since}', ignoring filter")

    for artifact_json in artifact_root.glob("*/artifact.json"):
        artifact_dir = artifact_json.parent
        mtime = artifact_json.stat().st_mtime

        # Filter by --since if specified
        if since_dt:
            mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            if mtime_dt < since_dt:
                continue

        artifact_info.append((artifact_dir, mtime))

    # Sort by mtime (newest first for limit mode)
    artifact_info.sort(key=lambda x: x[1], reverse=True)
    artifact_dirs = [info[0] for info in artifact_info]

    if not artifact_dirs:
        log("No artifacts found")
        # Still write a summary even if no artifacts
        empty_summary = {
            "job_id": job_id,
            "started_at": started,
            "finished_at": now_utc(),
            "artifacts_discovered": 0,
            "artifacts_validated": 0,
            "clean_artifacts": 0,
            "finding_counts": {"low": 0, "medium": 0, "high": 0},
            "findings": [],
            "rollups": {"counts_by_code": {}, "counts_by_severity": {}, "top_hosts": {}},
        }
        try:
            summary_path.write_text(json.dumps(empty_summary, indent=2), encoding="utf-8")
            latest_path.write_text(json.dumps(empty_summary, indent=2), encoding="utf-8")
        except Exception:
            pass
        return 0

    if args.seed is not None:
        random.seed(args.seed)

    # Determine which artifacts to validate
    total_discovered = len(artifact_dirs)

    if args.sample > 0:
        # Random sampling mode
        sample_dirs = random.sample(artifact_dirs, min(args.sample, len(artifact_dirs)))
    elif args.limit > 0:
        # Limit mode (take first N, which is newest due to mtime sort)
        sample_dirs = artifact_dirs[:args.limit]
    elif args.all or (args.sample == 0 and args.limit == 0):
        # All mode (default when no sampling/limit specified)
        sample_dirs = artifact_dirs
    else:
        sample_dirs = artifact_dirs

    log(f"Found {total_discovered} artifact(s), validating {len(sample_dirs)}")

    suspicious = compile_patterns(DEFAULT_SUSPICIOUS_PATTERNS)
    bad_urls = compile_patterns(DEFAULT_BAD_URL_PATTERNS)

    findings: List[Finding] = []
    clean = 0
    quarantined: List[str] = []

    # Build URL-to-artifact mapping for duplicate detection
    url_to_artifacts: Dict[str, List[Path]] = {}
    for d in sample_dirs:
        artifact_json = d / "artifact.json"
        if artifact_json.exists():
            try:
                meta = load_json(artifact_json)
                url = meta.get("url") or meta.get("source_url") or ""
                if url:
                    # Normalize URL for comparison (remove trailing slash, fragment)
                    normalized = url.rstrip("/").split("#")[0]
                    if normalized not in url_to_artifacts:
                        url_to_artifacts[normalized] = []
                    url_to_artifacts[normalized].append(d)
            except Exception:
                pass

    # Find duplicate URLs
    duplicate_urls = {url: dirs for url, dirs in url_to_artifacts.items() if len(dirs) > 1}

    for d in sample_dirs:
        fnds = validate_artifact(d, suspicious, bad_urls, args.max_chunks, args.min_chunk_chars, args.repetition_threshold)

        # Check for duplicate URL
        artifact_json = d / "artifact.json"
        if artifact_json.exists():
            try:
                meta = load_json(artifact_json)
                url = meta.get("url") or meta.get("source_url") or ""
                doc_id = meta.get("doc_id") or meta.get("id") or d.name
                if url:
                    normalized = url.rstrip("/").split("#")[0]
                    if normalized in duplicate_urls:
                        other_dirs = [str(od) for od in duplicate_urls[normalized] if od != d]
                        if other_dirs:
                            fnds.append(
                                Finding(
                                    severity="medium",
                                    code="DUPLICATE_URL",
                                    message=f"URL also found in {len(other_dirs)} other artifact(s)",
                                    doc_id=doc_id,
                                    url=url,
                                    artifact_dir=str(d),
                                    evidence=", ".join(other_dirs[:3]),
                                )
                            )
            except Exception:
                pass

        if fnds:
            findings.extend(fnds)
            if args.verbose:
                log(f"Issues in {d.name}: {len(fnds)} finding(s)")
            if args.quarantine:
                # Only quarantine high-severity findings
                has_high = any(f.severity == "high" for f in fnds)
                if has_high:
                    quarantine_dir.mkdir(parents=True, exist_ok=True)
                    ok = try_move_with_sudo(str(d), str(quarantine_dir))
                    if ok:
                        quarantined.append(d.name)
                        log(f"Quarantined {d.name} -> {quarantine_dir}")
                    else:
                        log(f"Failed to quarantine {d.name} (permission error)")
        else:
            clean += 1

    counts = {"low": 0, "medium": 0, "high": 0}
    for f in findings:
        counts[f.severity] += 1

    log(f"Validation complete: {clean} clean, {len(findings)} finding(s)")
    log(f"  High: {counts['high']}, Medium: {counts['medium']}, Low: {counts['low']}")

    summary = {
        "job_id": job_id,
        "started_at": started,
        "finished_at": now_utc(),
        "artifacts_discovered": total_discovered,
        "artifacts_validated": len(sample_dirs),
        "clean_artifacts": clean,
        "finding_counts": counts,
        "findings": [asdict(f) for f in findings],
    }

    rollups = summarize_findings(findings)

    # write rollups into summary
    summary["rollups"] = rollups
    if quarantined:
        summary["quarantined"] = quarantined

    summary_json = json.dumps(summary, indent=2)

    # Try write summary (permission-safe)
    try:
        summary_path.write_text(summary_json, encoding="utf-8")
        log(f"Summary written to {summary_path}")
    except PermissionError as e:
        log(f"ERROR: cannot write summary file ({summary_path}): {e}")
        # attempt sudo tee fallback
        try:
            subprocess.run(["sudo", "tee", str(summary_path)], input=summary_json.encode("utf-8"), check=False)
            log(f"Summary written to {summary_path} (via sudo)")
        except Exception as e2:
            log(f"ERROR: sudo fallback failed: {e2}")

    # Write validate_crawl_latest.json
    try:
        latest_path.write_text(summary_json, encoding="utf-8")
        log(f"Latest summary written to {latest_path}")
    except PermissionError as e:
        log(f"ERROR: cannot write latest file ({latest_path}): {e}")
        try:
            subprocess.run(["sudo", "tee", str(latest_path)], input=summary_json.encode("utf-8"), check=False)
            log(f"Latest summary written to {latest_path} (via sudo)")
        except Exception as e2:
            log(f"ERROR: sudo fallback failed: {e2}")

    if args.json_out:
        try:
            Path(args.json_out).write_text(summary_json, encoding="utf-8")
            log(f"Also wrote JSON output to {args.json_out}")
        except Exception as e:
            log(f"Could not write additional json-out {args.json_out}: {e}")

    # Pretty rollup logs (only if verbose)
    if args.verbose and rollups["counts_by_code"]:
        log("Top finding codes:")
        for k, v in rollups["counts_by_code"].items():
            log(f"  {k}: {v}")

        if rollups["top_hosts"]:
            log("Top hosts:")
            for k, v in rollups["top_hosts"].items():
                log(f"  {k}: {v}")

    fail = any(severity_at_least(f.severity, args.fail_on) for f in findings)
    log(f"Job completed (exit={'1' if fail else '0'})")

    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())

