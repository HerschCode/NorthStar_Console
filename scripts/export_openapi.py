"""Write openapi/p2.json (the /v1 contract the console generates its typed client from). CI fails if it drifts:
    python -m scripts.export_openapi && git diff --exit-code openapi/p2.json
"""
import json
from pathlib import Path

from src.api.main import app


def main():
    spec = app.openapi()
    spec["paths"] = {k: v for k, v in spec["paths"].items() if k.startswith("/v1")}
    Path("openapi").mkdir(exist_ok=True)
    Path("openapi/p2.json").write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
