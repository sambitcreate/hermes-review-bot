"""Findings contract: parse, gate, replace, compact, fingerprint."""
import pytest


def review_md(findings_json: str) -> str:
    return f"""### Summary

This PR renames the retry helper and wires it into the fetch path.

### Confidence Score: 4/5

4/5 — the fetch path was traced end-to-end; the error branch was not executed.

### Important Files Changed

- src/retry.ts — adds backoff wrapper used by fetch.ts.

### Findings

#### [P2] Retry loop never backs off after 429

- Where: `src/retry.ts:40`
- Mechanism: the delay stays 0 because the reset path runs before the sleep.
- Fix: move the reset after the sleep.

### Sequence Diagram

```mermaid
sequenceDiagram
  caller->>fetch: get(url)
  fetch->>retry: withBackoff()
```

<!-- hermes-review-findings-v1 -->
```json
{findings_json}
```
"""


VALID_ENTRY = """{
  "file": "src/retry.ts",
  "start_line": 40,
  "end_line": 40,
  "severity": "P2",
  "comment_type": "logic",
  "confidence": 0.9,
  "title": "Retry loop never backs off after 429",
  "mechanism": "delay is reset before the sleep on the 429 branch",
  "repair": "reset after sleeping"
}"""


def test_parse_valid_block_assigns_stable_id(make_review):
    m = make_review()
    findings = m.parse_findings_json(review_md(f"[{VALID_ENTRY}]"))
    assert findings and findings[0]["file"] == "src/retry.ts"
    assert findings[0]["id"] == m.finding_fingerprint("src/retry.ts", "Retry loop never backs off after 429")
    # stable across parses (ledger tracking depends on it)
    again = m.parse_findings_json(review_md(f"[{VALID_ENTRY}]"))
    assert again[0]["id"] == findings[0]["id"]


def test_missing_block_returns_none(make_review):
    m = make_review()
    assert m.extract_findings_block("### Summary\n\nNo hidden block here.") is None
    assert m.parse_findings_json("### Summary\n\nNo hidden block here.") is None


def test_malformed_block_raises(make_review):
    m = make_review()
    broken = review_md("{not json")
    with pytest.raises(RuntimeError):
        m.parse_findings_json(broken)
    not_array = review_md('{"file": "a"}')
    with pytest.raises(RuntimeError, match="not a JSON array"):
        m.parse_findings_json(not_array)
    missing_keys = review_md('[{"file": "a.ts"}]')
    with pytest.raises(RuntimeError, match="missing keys"):
        m.parse_findings_json(missing_keys)


def test_gate_strictness_floor_and_confidence(make_review):
    m = make_review()
    cfg = m.RepoConfig(strictness=2, confidence_threshold=0.6)
    findings = [
        {**_e("P3", "style", 0.95, "Ninja variable naming convention drift")},
        {**_e("P2", "logic", 0.4, "Cache invalidation race on refresh")},
        {**_e("P2", "logic", 0.9, "Error swallowed in fallback path")},
        {**_e("P1", "logic", 0.9, "Null deref when config missing")},
    ]
    kept, dropped = m.gate_findings(findings, cfg)
    titles = [f["title"] for f in kept]
    assert "Error swallowed in fallback path" in titles
    assert "Null deref when config missing" in titles
    assert "Ninja variable naming convention drift" not in titles          # below P2 floor
    assert "Cache invalidation race on refresh" not in titles              # conf 0.4 < 0.6
    reasons = {f["title"]: f["_drop_reason"] for f in dropped}
    assert "strictness floor" in reasons["Ninja variable naming convention drift"]
    assert "confidence" in reasons["Cache invalidation race on refresh"]


def test_gate_protects_security_and_p0p1_even_off_allowlist(make_review):
    m = make_review()
    cfg = m.RepoConfig(strictness=3, confidence_threshold=0.9, comment_types=["logic"])
    findings = [
        {**_e("P3", "style", 0.1, "Auth token persisted in plaintext cache")},
        {**_e("P1", "info", 0.2, "Session cookie missing expiry enforcement")},
        {**_e("P2", "style", 0.99, "Rename local variable for clarity")},
    ]
    kept, dropped = m.gate_findings(findings, cfg)
    titles = [f["title"] for f in kept]
    assert "Auth token persisted in plaintext cache" in titles   # security -> protected
    assert "Session cookie missing expiry enforcement" in titles  # P1 -> protected
    assert "Rename local variable for clarity" not in titles      # P2 below P1 floor (first gate to fire)
    dropped_by_title = {f["title"]: f["_drop_reason"] for f in dropped}
    assert "strictness floor" in dropped_by_title["Rename local variable for clarity"]


def test_gate_comment_type_allowlist(make_review):
    m = make_review()
    cfg = m.RepoConfig(strictness=1, comment_types=["logic"])
    kept, dropped = m.gate_findings([{**_e("P2", "info", 0.9, "Docs hint outdated example")}], cfg)
    assert not kept and dropped[0]["comment_type"] == "info"


def test_replace_findings_block_roundtrip(make_review):
    m = make_review()
    original = review_md(f"[{VALID_ENTRY}]")
    parsed = m.parse_findings_json(original)
    gated = parsed[:0]  # drop everything
    replaced = m.replace_findings_block(original, gated)
    assert m.parse_findings_json(replaced) == []
    assert m.FINDINGS_MARKER in replaced


def test_replace_appends_when_block_missing(make_review):
    m = make_review()
    out = m.replace_findings_block("### Summary\n\nSomething.", [])
    assert out.startswith("### Summary") and m.FINDINGS_MARKER in out


def test_compact_preserves_hidden_block(make_review):
    m = make_review()
    findings = m.parse_findings_json(review_md(f"[{VALID_ENTRY}]"))
    fid = findings[0]["id"]
    compacted = m.compact_findings_section(review_md(f"[{VALID_ENTRY}]"), {fid}, findings)
    assert m.FINDINGS_MARKER in compacted
    assert "### Findings" in compacted


def _e(severity, ctype, confidence, title):
    return {
        "file": "src/x.ts", "start_line": 1, "end_line": 1,
        "severity": severity, "comment_type": ctype, "confidence": confidence,
        "title": title, "mechanism": "m with counts 3 and 5", "repair": "r",
    }
