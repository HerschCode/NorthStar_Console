"""
End-to-end API latency benchmark against a LIVE server (real HTTP, real
uvicorn worker, real Postgres queries -- not an in-process function call).
No prediction-latency numbers existed anywhere in this repo before this;
an external review flagged that gap directly.

Usage:
    uvicorn src.api.main:app --port 8000 &
    python scripts/measure_api_latency.py --url http://localhost:8000 --api-key <key> --case-id <id>
"""
import argparse
import asyncio
import statistics
import time

import httpx

N_REQUESTS = 10


async def bench_endpoint(client: httpx.AsyncClient, url: str, name: str, headers: dict) -> dict:
    latencies = []
    status_codes = set()
    for _ in range(N_REQUESTS):
        t0 = time.perf_counter()
        resp = await client.get(url, headers=headers)
        latencies.append((time.perf_counter() - t0) * 1000)
        status_codes.add(resp.status_code)
    latencies.sort()

    def pctl(p):
        return latencies[min(int(len(latencies) * p), len(latencies) - 1)]

    return {
        "endpoint": name, "status_codes": sorted(status_codes),
        "p50_ms": round(pctl(0.50), 2), "p95_ms": round(pctl(0.95), 2),
        "p99_ms": round(pctl(0.99), 2), "max_ms": round(max(latencies), 2),
        "min_ms": round(min(latencies), 2),
    }


async def main_async(base_url: str, api_key: str, case_id: str):
    headers = {"X-API-Key": api_key}
    endpoints = {
        "GET /metrics/sla": f"{base_url}/metrics/sla",
        "GET /metrics/bottlenecks": f"{base_url}/metrics/bottlenecks",
        "GET /suppliers/performance": f"{base_url}/suppliers/performance",
        "GET /metrics/conformance": f"{base_url}/metrics/conformance",
        "GET /metrics/sla-risk-distribution": f"{base_url}/metrics/sla-risk-distribution",
        f"GET /orders/{{case_id}}/risk": f"{base_url}/orders/{case_id}/risk",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        # Warm up -- first hit after a cold connection/model-load shouldn't
        # pollute the measured numbers.
        await client.get(f"{base_url}/health")

        results = []
        for name, url in endpoints.items():
            r = await bench_endpoint(client, url, name, headers)
            results.append(r)
            print(f"{name:<35} p50={r['p50_ms']:>7.2f}ms  p95={r['p95_ms']:>7.2f}ms  "
                  f"p99={r['p99_ms']:>7.2f}ms  max={r['max_ms']:>7.2f}ms  status={r['status_codes']}")
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--case-id", required=True)
    args = parser.parse_args()
    asyncio.run(main_async(args.url, args.api_key, args.case_id))


if __name__ == "__main__":
    main()
