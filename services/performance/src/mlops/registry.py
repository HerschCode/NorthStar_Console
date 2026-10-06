"""File-based model registry: immutable versions, movable aliases (champion / challenger), an audit trail, rollback.

Deliberately dependency-free (stdlib only) so the same promote/rollback logic runs in CI, on a laptop and in a container; MLflow
can still be used for experiment tracking. Layout under `root`:
    events.jsonl                      append-only audit log (registered / alias_set / rolled_back / ...)
    <name>/v0001/<artifact>           the model file, never modified after registration
    <name>/v0001/record.json          metrics, metadata, data fingerprint, sha256 of the artifact
    <name>/v0001/meta.json            the training metadata the retrain trigger and drift check read
    <name>/aliases.json               {alias: [{version, at, reason}, ...]}, the last entry is current
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistryError(Exception):
    pass


class ModelRegistry:
    def __init__(self, root: str | Path = "models/registry"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- audit log -------------------------------------------------------------------------------------------------
    def log(self, event: str, **fields) -> dict:
        rec = {"at": _now(), "event": event, **fields}
        with open(self.root / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        return rec

    def events(self) -> list[dict]:
        p = self.root / "events.jsonl"
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()] if p.exists() else []

    # -- versions --------------------------------------------------------------------------------------------------
    def _dir(self, name: str) -> Path:
        return self.root / name

    def versions(self, name: str) -> list[str]:
        d = self._dir(name)
        return sorted(p.name for p in d.glob("v[0-9]*") if p.is_dir()) if d.exists() else []

    def register(self, name: str, artifact: str | Path, metrics: dict, meta: dict | None = None,
                 data_fingerprint: str | None = None, notes: str = "") -> dict:
        artifact = Path(artifact)
        if not artifact.is_file():
            raise RegistryError(f"artifact not found: {artifact}")
        existing = self.versions(name)
        version = f"v{(int(existing[-1][1:]) + 1) if existing else 1:04d}"
        vdir = self._dir(name) / version
        vdir.mkdir(parents=True)
        shutil.copy2(artifact, vdir / artifact.name)
        record = {
            "name": name, "version": version, "artifact": artifact.name, "sha256": sha256_file(vdir / artifact.name),
            "metrics": metrics, "data_fingerprint": data_fingerprint, "registered_at": _now(), "notes": notes,
        }
        (vdir / "record.json").write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        (vdir / "meta.json").write_text(json.dumps(meta or {}, indent=2, sort_keys=True), encoding="utf-8")
        self.log("registered", name=name, version=version, sha256=record["sha256"])
        return record

    def record(self, name: str, version: str) -> dict:
        p = self._dir(name) / version / "record.json"
        if not p.exists():
            raise RegistryError(f"{name} {version} is not registered")
        return json.loads(p.read_text(encoding="utf-8"))

    def artifact_path(self, name: str, version: str) -> Path:
        return self._dir(name) / version / self.record(name, version)["artifact"]

    def meta_path(self, name: str, version: str) -> Path:
        return self._dir(name) / version / "meta.json"

    def verify(self, name: str, version: str) -> bool:
        """True only if the stored file still has the sha256 recorded at registration (tamper / corruption check)."""
        return sha256_file(self.artifact_path(name, version)) == self.record(name, version)["sha256"]

    # -- aliases ---------------------------------------------------------------------------------------------------
    def _aliases(self, name: str) -> dict:
        p = self._dir(name) / "aliases.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def set_alias(self, name: str, alias: str, version: str, reason: str = "") -> None:
        self.record(name, version)  # must exist
        al = self._aliases(name)
        al.setdefault(alias, []).append({"version": version, "at": _now(), "reason": reason})
        (self._dir(name) / "aliases.json").write_text(json.dumps(al, indent=2, sort_keys=True), encoding="utf-8")
        self.log("alias_set", name=name, alias=alias, version=version, reason=reason)

    def resolve(self, name: str, alias: str = "champion") -> dict | None:
        hist = self._aliases(name).get(alias)
        return self.record(name, hist[-1]["version"]) if hist else None

    def alias_history(self, name: str, alias: str = "champion") -> list[dict]:
        return list(self._aliases(name).get(alias, []))

    def rollback(self, name: str, alias: str = "champion", reason: str = "") -> dict:
        """Point `alias` back at the version it held before the current one (a new history entry; history is never rewritten)."""
        hist = self.alias_history(name, alias)
        previous = [h["version"] for h in hist[:-1] if h["version"] != (hist[-1]["version"] if hist else None)]
        if not previous:
            raise RegistryError(f"no earlier version of {name}:{alias} to roll back to")
        target = previous[-1]
        self.set_alias(name, alias, target, reason=f"rollback: {reason}".strip())
        self.log("rolled_back", name=name, alias=alias, to=target, reason=reason)
        return self.record(name, target)
