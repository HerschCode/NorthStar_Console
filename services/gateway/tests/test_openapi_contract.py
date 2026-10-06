"""The console generates its typed client from openapi/p3.json, so the committed file has to be what the app publishes. (CI's `openapi-contract` job then
checks that a change to it is not a breaking one.)"""
import json
from pathlib import Path

from scripts.export_openapi import build_spec

SPEC = Path(__file__).resolve().parents[1] / "openapi" / "p3.json"


def test_the_committed_openapi_spec_is_what_the_app_publishes():
    committed = json.loads(SPEC.read_text(encoding="utf-8"))
    live = json.loads(json.dumps(build_spec(), sort_keys=True))
    assert committed == live, "openapi/p3.json is stale: run `python -m scripts.export_openapi` and commit it (the console's CI compares its pinned copy with main)"
