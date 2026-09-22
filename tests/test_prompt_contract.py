"""Prompt contract: bundled default + shipped example satisfy the parser's requirements."""
from pathlib import Path

import pytest

PROMPTS = {
    "default": Path(__file__).resolve().parent.parent / "prompts" / "review-default.md",
    "ios-example": Path(__file__).resolve().parent.parent / "prompts" / "examples" / "ios-review.md",
}


@pytest.mark.parametrize("name", ["default", "ios-example"])
def test_required_headings_present_in_order(make_review, name):
    m = make_review()
    text = PROMPTS[name].read_text(encoding="utf-8")
    positions = []
    for heading in m.REQUIRED_REVIEW_HEADINGS:
        pos = text.find(heading)
        assert pos != -1, f"{name}: missing required heading {heading!r}"
        positions.append(pos)
    assert positions == sorted(positions), f"{name}: headings out of required order"


@pytest.mark.parametrize("name", ["default", "ios-example"])
def test_findings_marker_and_contract_keys(make_review, name):
    m = make_review()
    text = PROMPTS[name].read_text(encoding="utf-8")
    assert m.FINDINGS_MARKER in text, f"{name}: findings marker missing"
    for key in m.REQUIRED_FINDING_KEYS:
        assert f'"{key}"' in text, f"{name}: contract key {key!r} missing"
    for sev in ("P0", "P1", "P2", "P3"):
        assert sev in text, f"{name}: severity {sev!r} not documented"
    for ctype in m.VALID_COMMENT_TYPES:
        assert f"`{ctype}`" in text, f"{name}: comment type {ctype!r} missing"


@pytest.mark.parametrize("name", ["default", "ios-example"])
def test_no_stale_references(make_review, name):
    text = PROMPTS[name].read_text(encoding="utf-8").lower()
    for stale in ("agy-findings", "pre-computed impact", "semantic vector index", ".agy/"):
        assert stale not in text, f"{name}: stale reference {stale!r}"


def test_bundled_default_loads_via_loader(make_review):
    m = make_review()
    m.load_global_config()
    text = m.load_prompt()
    assert text.startswith("# Hermes Review Bot")
