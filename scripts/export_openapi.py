"""Write openapi/p1.json (the /v1 contract the console generates its typed client from) without building the snapshot.

    python -m scripts.export_openapi

CI checks that the committed file is what the app publishes (tests/test_v1_api.py) and that a change to it is not a breaking one (the openapi-contract job).
"""
import json
from pathlib import Path

from src.api.main import app


def build_spec() -> dict:
    """The published contract: the /v1 surface only."""
    spec = {**app.openapi()}
    spec["paths"] = {k: v for k, v in spec["paths"].items() if k.startswith("/v1")}
    return spec


def main() -> None:
    Path("openapi").mkdir(exist_ok=True)
    spec = build_spec()
    Path("openapi/p1.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    print(len(spec["paths"]), "paths")


if __name__ == "__main__":
    main()
