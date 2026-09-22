"""Shared fixtures: load scripts/review.py against an isolated HERMES_HOME."""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "review.py"


@pytest.fixture
def make_review(tmp_path, monkeypatch):
    """Factory: load a fresh review.py module with isolated HOME + optional config."""

    def _make(config_text: str | None = None):
        hh = tmp_path / "hermes"
        hh.mkdir(exist_ok=True)
        monkeypatch.setenv("HERMES_HOME", str(hh))
        monkeypatch.setenv("HERMES_REVIEW_BOT_CONFIG", str(hh / "review-bot" / "config.yaml"))
        monkeypatch.delenv("HERMES_PR_REVIEW_REPO", raising=False)
        monkeypatch.delenv("HERMES_PR_REVIEW_PROMPT", raising=False)
        if config_text is not None:
            (hh / "review-bot").mkdir(exist_ok=True)
            (hh / "review-bot" / "config.yaml").write_text(config_text, encoding="utf-8")
        sys.modules.pop("review", None)
        spec = importlib.util.spec_from_file_location("review", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["review"] = mod
        spec.loader.exec_module(mod)
        return mod

    return _make
