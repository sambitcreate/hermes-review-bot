"""GitHub X-Hub-Signature-256 contract (what the gateway verifies, fail-closed)."""
import hashlib
import hmac
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from signing import compute_signature, verify_signature  # noqa: E402

PAYLOAD = {
    "action": "opened",
    "number": 7,
    "pull_request": {"number": 7, "head": {"sha": "a" * 40}},
    "repository": {"full_name": "Foo/Bar"},
}
SECRET = "0123456789abcdef" * 4  # openssl rand -hex 32 shape: 64 hex chars


def test_secret_shape_setup_generates():
    assert re.fullmatch(r"[0-9a-f]{64}", SECRET)


def test_signature_matches_github_algorithm_oracle():
    body = json.dumps(PAYLOAD, separators=(",", ":"))
    expected = "sha256=" + hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert compute_signature(SECRET, body) == expected
    assert re.fullmatch(r"sha256=[0-9a-f]{64}", compute_signature(SECRET, body))


def test_verify_roundtrip_and_tamper():
    body = json.dumps(PAYLOAD)
    header = compute_signature(SECRET, body)
    assert verify_signature(SECRET, body, header)
    assert not verify_signature(SECRET, body + " ", header)   # payload tamper
    assert not verify_signature("other-secret", body, header)  # secret mismatch
    assert not verify_signature(SECRET, body, None)            # missing header -> fail closed
    assert not verify_signature(SECRET, body, "sha256=deadbeef")
