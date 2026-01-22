#!/usr/bin/env python3
"""
ragai/tools/ingest_benchmark.py

Client-side ingest benchmark tool for ragai.

Drop into: ragai/tools/ingest_benchmark.py

What it does
- Issues concurrent embedding requests to one or more embedding endpoints (default tries common paths)
- Samples GPU utilization via `nvidia-smi` (if available) and process counts via `pgrep` / `ps`
- Runs a parameter sweep across concurrency × batch_size × endpoints (configurable)
- Logs JSON results to data/logs/benchmark_<timestamp>.json and a rolling .log
- Prints progress to stdout

Notes
- Designed as a test-only tool (doesn't touch DB or real ingest)
- If your embedding API expects a different JSON body shape, edit the `batch_payload` construction in run_one_configuration()
"""

import argparse
import asyncio
import json
import os
import time
import subprocess
import shutil
from datetime import datetime
from itertools import product
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------
# Defaults & config
# ---------------------------
DEFAULT_ENDPOINTS = ["/api/embeddings", "/api/embed", "/api/embeddings/batch"]
DEFAULT_CONCURRENCY = [1, 2, 3]
DEFAULT_BATCH_SIZES = [1]
DEFAULT_DURATION = 20
DEFAULT_REPEATS = 1
DEFAULT_SAMPLE_INTERVAL = 1.0
LOG_DIR = "data/logs"

# ---------------------------
# System sampling helpers
# ---------------------------
def has_nvidia_smi() -> bool:
    return shutil.which("nvidia-smi") is not None

def sample_gpu_util() -> Optional[int]:
    """Return average GPU utilization percent or None if unavailable."""
    if not has_nvidia_smi():
        return None
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        vals = [int(x.strip()) for x in out.decode().splitlines() if x.strip()]
        if not vals:
            return None
        return sum(vals) // len(vals)
    except Exception:
        return None

def count_processes_matching(pattern: str) -> Optional[int]:
    """Return number of processes matching pattern using pgrep -f if available, else ps fallback."""
    if not pattern:
        return None
    try:
        if shutil.which("pgrep"):
            out = subprocess.check_output(["pgrep", "-f", pattern])
            return len([l for l in out.decode().splitlines() if l.strip()])
        else:
            ps = subprocess.check_output(["ps", "aux"], stderr=subprocess.DEVNULL).decode()
            matched = [l for l in ps.splitlines() if pattern in l and "grep" not in l]
            return len(matched)
    except subprocess.CalledProcessError:
        # pgrep returns non-zero when no matches
        return 0
    except Exception:
        return None

# ---------------------------
# Benchmark core
# ---------------------------
class BenchmarkResult:
    def __init__(self):
        self.total_requests = 0
        self.samples: List[Dict[str, Any]] = []
        self.start_time = 0.0
        self.end_time = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    def to_dict(self) -> Dict[str, Any]:
        avg_rate = (self.total_requests / self.duration) if self.duration > 0 else 0.0
        return {
            "total_requests": self.total_requests,
            "duration_s": self.duration,
            "avg_requests_per_s": avg_rate,
            "samples": self.samples,
        }

async def _single_worker_loop(client: httpx.AsyncClient, url: str, batch_payload: Any, stop_at: float, sem: asyncio.Semaphore, counters: Dict[str,int]):
    """
    Repeatedly send requests until stop_at time.
    Increments counters['count'] on success, tracks errors/exceptions.
    """
    while time.time() < stop_at:
        try:
            async with sem:
                r = await client.post(url, json=batch_payload, timeout=120.0)
                if r.status_code == 200:
                    counters["count"] += 1
                else:
                    counters["errors"] += 1
        except Exception:
            counters["exceptions"] += 1
            await asyncio.sleep(0.05)

async def run_one_configuration(host: str, endpoint: str, concurrency: int, batch_size: int, duration: int, sample_interval: float, process_pattern: Optional[str]) -> BenchmarkResult:
    """
    Run a single benchmark configuration.
    """
    url = host.rstrip("/") + endpoint
    result = BenchmarkResult()
    result.start_time = time.time()
    stop_at = time.time() + duration

    # Prepare payload for Ollama: { "model": "<model>", "prompt": <string or [strings]> }
    model_name = getattr(run_one_configuration, "_model_name", "nomic-embed-text")

    if batch_size == 1:
        batch_payload = {"model": model_name, "prompt": "benchmarking embedding"}
    else:
        batch_payload = {"model": model_name, "prompt": ["benchmarking embedding"] * batch_size}

    sem = asyncio.Semaphore(concurrency)
    counters = {"count": 0, "errors": 0, "exceptions": 0}

    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [asyncio.create_task(_single_worker_loop(client, url, batch_payload, stop_at, sem, counters)) for _ in range(concurrency)]

        last_count = 0
        while time.time() < stop_at:
            await asyncio.sleep(sample_interval)
            now = time.time()
            current_count = counters["count"]
            interval_requests = current_count - last_count
            last_count = current_count
            gpu = sample_gpu_util()
            process_count = count_processes_matching(process_pattern) if process_pattern else None
            elapsed = now - result.start_time
            result.samples.append({
                "t": round(elapsed, 3),
                "gpu": gpu,
                "process_count": process_count,
                "interval_requests": interval_requests,
                "total_requests": current_count,
                "errors": counters["errors"],
                "exceptions": counters["exceptions"],
            })
            # Console progress
            print(f"[{endpoint}] t={elapsed:.1f}s concurrency={concurrency} batch={batch_size} interval_req={interval_requests} total={current_count} gpu={gpu} proc={process_count}")

        # politely cancel workers
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    result.end_time = time.time()
    result.total_requests = counters["count"]
    return result

# ---------------------------
# Sweep orchestrator
# ---------------------------
def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)

def timestamped_filename(prefix: str, ext: str = ".json"):
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe_prefix = prefix.replace(" ", "_").replace("/", "_")
    return os.path.join(LOG_DIR, f"{safe_prefix}_{ts}{ext}")

async def run_sweep_and_pick_best(host: str,
                                  endpoints: List[str],
                                  concurrencies: List[int],
                                  batch_sizes: List[int],
                                  duration: int,
                                  sample_interval: float,
                                  repeats: int,
                                  process_pattern: Optional[str],
                                  model_name: str):
    ensure_log_dir()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    overall_log: Dict[str, Any] = {
        "run_id": run_id,
        "host": host,
        "endpoints_tried": endpoints,
        "timestamp": run_id,
        "results": []
    }

    for endpoint in endpoints:
        for (c, b) in product(concurrencies, batch_sizes):
            for r in range(repeats):
                print(f"\n=== RUN endpoint={endpoint} concurrency={c} batch={b} repeat={r+1}/{repeats} duration={duration}s ===")
                # Tell the worker which model to use
                run_one_configuration._model_name = model_name
                res = await run_one_configuration(host, endpoint, concurrency=c, batch_size=b, duration=duration, sample_interval=sample_interval, process_pattern=process_pattern)
                res_dict = res.to_dict()
                entry = {
                    "endpoint": endpoint,
                    "concurrency": c,
                    "batch_size": b,
                    "repeat": r+1,
                    "result": res_dict,
                }
                overall_log["results"].append(entry)
                jsonfile = timestamped_filename(f"benchmark_{endpoint.strip('/').replace('/','_')}_c{c}_b{b}_r{r+1}", ".json")
                with open(jsonfile, "w") as fh:
                    json.dump(entry, fh, indent=2)
                logtxt = timestamped_filename("benchmark_summary", ".log")
                with open(logtxt, "a") as fh:
                    fh.write(json.dumps({
                        "ts": datetime.utcnow().isoformat(),
                        "endpoint": endpoint,
                        "concurrency": c,
                        "batch_size": b,
                        "repeat": r+1,
                        "total_requests": res.total_requests,
                        "duration_s": res.duration,
                        "avg_rps": res_dict["avg_requests_per_s"],
                    }) + "\n")

    # choose best by avg_requests_per_s, prefer lower concurrency on ties
    best: Optional[Dict[str, Any]] = None
    for e in overall_log["results"]:
        avg_rps = e["result"]["avg_requests_per_s"]
        key = (avg_rps, -e["concurrency"])
        if best is None or key > (best["result"]["avg_requests_per_s"], -best["concurrency"]):
            best = e
    overall_log["best"] = best
    overall_file = timestamped_filename("benchmark_overall", ".json")
    with open(overall_file, "w") as fh:
        json.dump(overall_log, fh, indent=2)
    print("\n=== SWEEP COMPLETE ===")
    if best:
        print("Best configuration (by avg requests/sec):")
        print(json.dumps(best, indent=2))
    else:
        print("No successful runs recorded.")
    return overall_log

# ---------------------------
# CLI entrypoint
# ---------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Ragai ingest benchmark runner")
    p.add_argument("--host", type=str, default="http://localhost:8000", help="Base host for API (no trailing slash)")
    p.add_argument("--endpoint", type=str, nargs="+", default=DEFAULT_ENDPOINTS, help="Endpoint paths to try (e.g. /api/embeddings)")
    p.add_argument("--concurrency", type=int, nargs="+", default=DEFAULT_CONCURRENCY, help="Concurrency values to try (space separated)")
    p.add_argument("--batch-size", type=int, nargs="+", default=DEFAULT_BATCH_SIZES, help="Batch sizes to try (space separated)")
    p.add_argument("--duration", type=int, default=DEFAULT_DURATION, help="Duration per run (seconds)")
    p.add_argument("--repeats", type=int, default=DEFAULT_REPEATS, help="Number of repeats per config")
    p.add_argument("--sample-interval", type=float, default=DEFAULT_SAMPLE_INTERVAL, help="Interval (s) between GPU/process samples")
    p.add_argument("--process-pattern", type=str, default="", help="Process match pattern (pgrep -f style) to count processes (eg 'ollama' or 'python')")
    p.add_argument("--model", type=str, default="nomic-embed-text", help="Model name to request from Ollama (e.g. nomic-embed-text)")
    return p.parse_args()


def main():
    args = parse_args()
    endpoints = [e if e.startswith("/") else f"/{e}" for e in args.endpoint]
    print(f"Starting ingest benchmark: host={args.host} endpoints={endpoints}")
    if has_nvidia_smi():
        print("nvidia-smi found: GPU sampling enabled")
    else:
        print("nvidia-smi not found: GPU sampling disabled")
    if args.process_pattern:
        print(f"Process matching enabled for pattern: {args.process_pattern}")
    asyncio.run(run_sweep_and_pick_best(
        host=args.host,
        endpoints=endpoints,
        concurrencies=args.concurrency,
        batch_sizes=args.batch_size,
        duration=args.duration,
        sample_interval=args.sample_interval,
        repeats=args.repeats,
        process_pattern=args.process_pattern if args.process_pattern else None,
        model_name=args.model,
    ))

if __name__ == "__main__":
    main()
