import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_jailbreak_llms_download_uses_a_pinned_commit():
    spec = importlib.util.spec_from_file_location("prepare_training_data", REPO / "scripts" / "prepare_training_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.JBLLMS_TARBALL_URL.startswith("https://codeload.github.com/verazuo/jailbreak_llms/tar.gz/")
    assert module.JBLLMS_TARBALL_URL.endswith(module.JBLLMS_COMMIT_SHA)
    assert module.JBLLMS_COMMIT_SHA != "main"
