"""
Streams the real BPI Challenge 2019 event log (4TU.ResearchData, 729MB IEEE-XES XML,
1.6M events / 251K cases) and writes out a sample as a CSV in the shape
src/ingestion/load_event_log.py already expects -- WITHOUT downloading the whole
file. The connection is closed as soon as the stopping condition is met, so this stays
fast and fits comfortably in a free-tier Postgres instance (Neon's free tier is 0.5GB;
the full 251K-case log would not fit, a genuine constraint of this deployment, not a
reason to fake the data -- these are real events from real cases in the real dataset,
just not all 251K of them).

Uses stdlib xml.etree.ElementTree.iterparse on the streamed response body rather than
loading the file into memory -- a <trace> element is only fully buffered by iterparse
once its closing tag arrives, and is discarded (element.clear()) immediately after each
case is written, so memory stays bounded regardless of how large the source file is.

2026-09-28 (finance module, Phase F1): extended to keep the AP-control fields that the
original 8-column sample dropped -- confirmed present in the real source XES by probing
the first traces (see docs/data-contract.md). Also switched from "first N cases" to a
coverage-stopping sample: the source file's case ordering groups a vendor's purchasing
documents together for long stretches, so "first N" landed hundreds of vendors with only
1-2 POs each -- useless for a threshold-splitting or duplicate-invoice check that needs
several POs per vendor to have anything to compare. The new stop condition keeps
streaming until MIN_VENDORS_WITH_ENOUGH_POS vendors each have >= MIN_POS_PER_VENDOR
distinct purchasing documents, or MAX_CASES is reached, whichever comes first -- still a
sample of the real file in its real order, not reshuffled or filtered by content.

The 4TU connection drops with an SSL error on some networks partway through a long
stream (observed here, unrelated to this project's code) -- download_sample retries the
whole stream from scratch (no partial/resumable download exists for this endpoint) up to
MAX_ATTEMPTS times with backoff.
"""
import csv
import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import requests

BPI2019_URL = (
    "https://data.4tu.nl/file/35ed7122-966a-484e-a0e1-749b64e3366d/"
    "864493d1-3a58-47f6-ad6f-27f95f995828"
)

# The event-level and case-level (trace) attribute keys src/ingestion/load_event_log.py
# looks for, per RAW_COLUMN_MAP. Anything else in the source XES is real but unused by
# this pipeline and dropped here rather than carried through unused. Verified present in
# the real source (not assumed) by streaming and inspecting the first traces' attribute
# keys before writing this list.
EVENT_KEYS = ["concept:name", "time:timestamp", "org:resource", "User", "Cumulative net worth (EUR)"]
CASE_KEYS = [
    "concept:name", "Purchasing Document", "Item", "Spend area text", "Vendor",
    "Item Category", "GR-Based Inv. Verif.", "Goods Receipt", "Document Type",
    "Item Type", "Company", "Sub spend area text",
]
FIELDNAMES = [f"case:{k}" for k in CASE_KEYS] + [k for k in EVENT_KEYS]

MAX_CASES = 20_000            # hard cap regardless of coverage, so this always terminates
MIN_POS_PER_VENDOR = 10       # "well represented" per the F1 spec
MIN_VENDORS_COVERED = 60      # stop once this many vendors clear MIN_POS_PER_VENDOR
MAX_ATTEMPTS = 10


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_attrs(elem) -> dict:
    """Case-level (trace) or event-level attributes are direct <string>/<date>/...
    children with key="..." value="..." -- nested <event> children of a <trace> are a
    different tag and are correctly ignored here."""
    attrs = {}
    for child in elem:
        if _localname(child.tag) in ("string", "date", "int", "float", "boolean"):
            key = child.get("key")
            if key:
                attrs[key] = child.get("value")
    return attrs


def _stream_once(n_cases_cap: int, out_path: Path, url: str) -> tuple[int, int, dict]:
    cases_written = 0
    rows_written = 0
    vendor_po_counts: dict[str, set] = defaultdict(set)
    vendors_covered = 0

    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        response.raw.decode_content = True

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()

            context = ET.iterparse(response.raw, events=("end",))
            for _, elem in context:
                if _localname(elem.tag) != "trace":
                    continue

                case_attrs = _direct_attrs(elem)
                case_row_prefix = {f"case:{k}": case_attrs.get(k, "") for k in CASE_KEYS}

                for event_elem in elem:
                    if _localname(event_elem.tag) != "event":
                        continue
                    event_attrs = _direct_attrs(event_elem)
                    row = dict(case_row_prefix)
                    row.update({k: event_attrs.get(k, "") for k in EVENT_KEYS})
                    writer.writerow(row)
                    rows_written += 1

                cases_written += 1
                vendor = case_attrs.get("Vendor")
                po = case_attrs.get("Purchasing Document")
                if vendor and po:
                    before = len(vendor_po_counts[vendor])
                    vendor_po_counts[vendor].add(po)
                    if before < MIN_POS_PER_VENDOR <= len(vendor_po_counts[vendor]):
                        vendors_covered += 1
                elem.clear()  # bound memory -- don't keep every parsed trace around

                if cases_written % 1000 == 0:
                    print(f"  ...{cases_written} cases, {rows_written} events, "
                          f"{vendors_covered} vendors with >={MIN_POS_PER_VENDOR} POs so far")

                if vendors_covered >= MIN_VENDORS_COVERED or cases_written >= n_cases_cap:
                    break  # closes the response (the `with` block) without reading the rest

    method = {
        "sampling_method": "streamed in source-file order (not reshuffled/filtered by content); "
                           "stopped at coverage target or the hard cap, whichever came first",
        "stop_reason": "vendor_coverage_target" if vendors_covered >= MIN_VENDORS_COVERED else "max_cases_cap",
        "min_pos_per_vendor": MIN_POS_PER_VENDOR,
        "vendors_meeting_target": vendors_covered,
        "min_vendors_covered_target": MIN_VENDORS_COVERED,
        "distinct_vendors_seen": len(vendor_po_counts),
    }
    return cases_written, rows_written, method


def download_sample(n_cases_cap: int, out_path: Path, url: str = BPI2019_URL) -> tuple[int, int, dict]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _stream_once(n_cases_cap, out_path, url)
        except (requests.exceptions.RequestException, OSError) as exc:
            last_exc = exc
            wait = 5 * attempt
            print(f"  stream attempt {attempt}/{MAX_ATTEMPTS} failed ({exc!r}); retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"download failed after {MAX_ATTEMPTS} attempts") from last_exc


def main():
    n_cases_cap = int(os.environ.get("BPI_SAMPLE_CASES", str(MAX_CASES)))
    out_path = Path(os.environ.get("RAW_EVENT_LOG_PATH", "data/raw/bpi2019_events.csv"))
    print(f"Streaming real BPI Challenge 2019 data from 4TU, sampling until "
          f"{MIN_VENDORS_COVERED} vendors have >={MIN_POS_PER_VENDOR} POs or {n_cases_cap} cases...")
    cases, rows, method = download_sample(n_cases_cap, out_path)
    print(f"Done: {cases} real cases, {rows} real events written to {out_path}")
    print(f"Sampling method: {method}")
    import json
    Path(out_path).with_suffix(".sample_method.json").write_text(json.dumps(method, indent=2))


if __name__ == "__main__":
    sys.exit(main())
