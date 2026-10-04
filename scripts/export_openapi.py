"""Write openapi/p3.json (the /v1 contract the console generates its typed client from)."""
import json
import os
from pathlib import Path

os.environ.setdefault("GATEWAY_LOG_STDOUT", "0")

from gateway.app import app  # noqa: E402


def main():
    spec = app.openapi()
    spec["paths"] = {k: v for k, v in spec["paths"].items() if k.startswith("/v1")}
    Path("openapi").mkdir(exist_ok=True)
    Path("openapi/p3.json").write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(len(spec["paths"]), "paths")


if __name__ == "__main__":
    main()
