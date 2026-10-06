"""Load a model artifact only if (1) its sha256 matches the registry record and (2) its pickle stream imports nothing outside an allowlist.

joblib/pickle files can run arbitrary code on load. Hash verification stops a swapped file; the restricted unpickler stops a file that
was *registered* with a malicious payload (or one fetched from an untrusted source). The allowlist is the scikit-learn / numpy / scipy /
pandas types a fitted model needs, plus this project's own estimator wrappers. Anything else raises `UnsafeModelError`.
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest import mock

import joblib
from joblib import numpy_pickle

from .registry import ModelRegistry, sha256_file


class UnsafeModelError(Exception):
    pass


ALLOWED_PREFIXES = ("sklearn.", "numpy.", "scipy.", "pandas.", "src.ml.")
DENIED_PREFIXES = (
    "numpy.testing", "numpy.f2py", "numpy.distutils", "numpy.ctypeslib", "numpy.lib.npyio", "numpy.lib._npyio_impl",
    "pandas.io", "pandas.compat.pickle_compat", "pandas._testing", "scipy.io", "sklearn.utils._testing", "sklearn.externals",
)
ALLOWED_EXACT = {
    ("joblib.numpy_pickle", "NumpyArrayWrapper"), ("joblib.numpy_pickle", "NumpyArrayWrapper"),
    ("collections", "OrderedDict"), ("collections", "defaultdict"), ("collections", "deque"),
    ("builtins", "set"), ("builtins", "frozenset"), ("builtins", "dict"), ("builtins", "list"), ("builtins", "tuple"),
    ("builtins", "int"), ("builtins", "float"), ("builtins", "str"), ("builtins", "bytes"), ("builtins", "bytearray"),
    ("builtins", "complex"), ("builtins", "bool"), ("builtins", "object"), ("builtins", "slice"), ("builtins", "range"),
    ("_codecs", "encode"),
}
_LOCK = threading.Lock()


def _allowed(module: str, name: str) -> bool:
    if (module, name) in ALLOWED_EXACT:
        return True
    if module == "_loss" and name.startswith("Cy"):  # scikit-learn's compiled loss functions (top-level extension module)
        return True
    if any(module == d or module.startswith(d + ".") or module.startswith(d) for d in DENIED_PREFIXES):
        return False
    return module.startswith(ALLOWED_PREFIXES) or module in ("numpy", "sklearn", "scipy", "pandas")


def _restricted_unpickler_class():
    class Restricted(numpy_pickle.NumpyUnpickler):
        def find_class(self, module, name):
            if not _allowed(module, name):
                raise UnsafeModelError(f"blocked import while loading model: {module}.{name}")
            return super().find_class(module, name)
    return Restricted


def restricted_load(path: str | Path):
    """joblib.load with the allowlist enforced. Serialized under a lock because it patches joblib's unpickler for the call."""
    with _LOCK, mock.patch.object(numpy_pickle, "NumpyUnpickler", _restricted_unpickler_class()):
        return joblib.load(path)


def load_verified(registry: ModelRegistry, name: str, alias: str = "champion"):
    """Resolve alias -> verify sha256 against the registry record -> restricted load. Returns (model, record)."""
    rec = registry.resolve(name, alias)
    if rec is None:
        raise UnsafeModelError(f"{name}:{alias} is not registered")
    path = registry.artifact_path(name, rec["version"])
    if sha256_file(path) != rec["sha256"]:
        raise UnsafeModelError(f"{name} {rec['version']}: file hash does not match the registry record")
    return restricted_load(path), rec
