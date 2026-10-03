"""
Does the gateway, with the student layer on, run inside 512 MB? Measured by running the real app (uvicorn gateway.app:app) under a Windows job-object commit limit.

The container test (`docker run -m 512m`) has never been possible here: no Docker daemon. This is the substitute, and it is not the same thing, so what it is and is not:
  * a Windows job object caps the COMMITTED memory (private bytes: heap, stacks, private DLL data) of the process; allocations past the cap fail, which Python surfaces as MemoryError
    and a crashed worker. A Linux cgroup caps RSS plus page cache and kills the process; shared-library pages count towards RSS there, file-backed model pages can be reclaimed.
    Commit is usually at least the working set, so passing a cap here is a reasonable, not a conclusive, sign. The real check remains `docker run -m 512m` on the Render image.
  * The app is driven with real traffic (the held-out benign and attack texts, long prompts up to the 20,000-character limit, 8 concurrent clients) so the peak includes request handling.
  * Peaks are the OS's own peak counters (peak working set, peak pagefile/commit), read after the run, not a sampled maximum.

    python -X utf8 -m scripts.measure_memory_cap [--caps 512 384 320 256] [--backends numpy student both]

Writes reports/p3_guard_student_memory.json. Windows only (pywin32). Not used by CI.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
REPORT = REPO_ROOT / "reports" / "p3_guard_student_memory.json"
PORT = 8141
N_REQUESTS = 400
CLIENTS = 8


def make_job(limit_mb: int):
    import win32job
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= (win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
                                                    | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    info["ProcessMemoryLimit"] = info["JobMemoryLimit"] = limit_mb * 2**20
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    return job


def traffic():
    from scripts.retrain_classifier_v2 import load_sources
    _, held = load_sources()
    pool = [t for k in ("own_corpus", "deepset_test", "jbb_benign", "short_benign_heldout", "in_domain_heldout", "jbllms_clean", "safeguard_test", "jackhhao_test")
            for t, _ in held[k]]
    import random
    random.Random(0).shuffle(pool)
    texts = [t[:20000] for t in pool[:N_REQUESTS - 5]]
    texts += ["Please summarise this report. " * 700] * 5                  # ~20,000 characters of ordinary text: the worst case for length
    return texts


def post(i, text):
    body = json.dumps({"prompt": text, "session_id": f"mem-{i}", "role": "employee", "backend": "stub_ops_agent", "user_id": "mem"}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/gateway/chat", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read()).get("allowed")
    except Exception as exc:  # noqa: BLE001 -- a failed request is a result
        return type(exc).__name__, None


def run_one(backend: str, cap_mb: int, texts) -> dict:
    import win32api
    import win32con
    import win32job
    env = {**os.environ, "CLASSIFIER_BACKEND": backend, "MODEL_INTEGRITY": "enforce", "GATEWAY_LITE": "0", "EMBEDDING_BACKEND": "none",
           "GATEWAY_IP_RATE_LIMIT": "0", "GATEWAY_LOG_STDOUT": "0", "PYTHONUNBUFFERED": "1"}
    job = make_job(cap_mb)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "gateway.app:app", "--host", "127.0.0.1", "--port", str(PORT)], cwd=REPO_ROOT, env=env,  # nosec B603 - fixed argv, no shell
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    win32job.AssignProcessToJobObject(job, win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, proc.pid))
    ps = psutil.Process(proc.pid)
    result = {"backend": backend, "cap_mb": cap_mb, "started": False, "survived": False}
    try:
        t0 = time.time()
        while time.time() - t0 < 90:
            if proc.poll() is not None:
                result["exit"] = f"process exited with code {proc.returncode} during start-up"
                result["stderr_tail"] = proc.stderr.read().decode("utf-8", "replace")[-400:]
                return result
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).read()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        else:
            result["exit"] = "no /health within 90 s"
            return result
        result["started"] = True
        result["startup_s"] = round(time.time() - t0, 1)
        mi = ps.memory_info()
        result["idle"] = {"working_set_mb": round(mi.rss / 2**20, 1), "commit_mb": round(mi.private / 2**20, 1)}
        t1 = time.time()
        with ThreadPoolExecutor(CLIENTS) as pool:
            outcomes = list(pool.map(lambda a: post(*a), enumerate(texts)))
        elapsed = time.time() - t1
        mi = ps.memory_info()
        result["requests"] = {"n": len(outcomes), "ok_200": sum(s == 200 for s, _ in outcomes), "blocked": sum(a is False for _, a in outcomes),
                              "errors": sorted({str(s) for s, _ in outcomes if s != 200}), "seconds": round(elapsed, 1), "req_per_s": round(len(outcomes) / elapsed, 1)}
        result["peak"] = {"working_set_mb": round(mi.peak_wset / 2**20, 1), "commit_mb": round(mi.peak_pagefile / 2**20, 1)}
        result["after"] = {"working_set_mb": round(mi.rss / 2**20, 1), "commit_mb": round(mi.private / 2**20, 1)}
        result["survived"] = proc.poll() is None and result["requests"]["ok_200"] == len(outcomes)
        return result
    finally:
        proc.kill()
        proc.wait(timeout=15)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--caps", nargs="+", type=int, default=[512, 384, 320, 256])
    ap.add_argument("--backends", nargs="+", default=["numpy", "student", "both"])
    args = ap.parse_args()
    texts = traffic()
    out = {"method": __doc__.strip().split("\n\n")[0], "n_requests": N_REQUESTS, "clients": CLIENTS, "runs": []}
    for backend in args.backends:
        for cap in args.caps:
            r = run_one(backend, cap, texts)
            out["runs"].append(r)
            line = f"{backend:<8} cap {cap:>4} MB: " + ("survived" if r["survived"] else f"FAILED ({r.get('exit') or r.get('requests', {}).get('errors')})")
            if "peak" in r:
                line += f" | peak commit {r['peak']['commit_mb']} MB, peak working set {r['peak']['working_set_mb']} MB | {r['requests']['req_per_s']} req/s"
            print(line, flush=True)
            if not r["survived"]:
                break                                                       # a lower cap cannot do better for this backend
    REPORT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {REPORT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
