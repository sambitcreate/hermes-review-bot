"""GitHub webhook signature helpers.

Documents and locks the HMAC contract that the Hermes gateway enforces on
every webhook route: GitHub sends `X-Hub-Signature-256: sha256=<hex>` computed
as HMAC-SHA256(secret, raw_request_body); the gateway recomputes it with
hmac.compare_digest and rejects mismatches (and rejects routes that have no
secret at all — fail closed). Route scripts never see unverified payloads, so
the handler itself does no crypto; this module exists so setup.sh's secret and
the README's manual-webhook steps stay verifiable by the test suite.
"""
from __future__ import annotations

import hashlib
import hmac


def compute_signature(secret: str | bytes, payload: str | bytes) -> str:
    """GitHub's X-Hub-Signature-256 value for payload under secret."""
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_signature(secret: str | bytes, payload: str | bytes, header: str | None) -> bool:
    """Constant-time check of a GitHub signature header (gateway semantics)."""
    if not header:
        return False  # fail closed without a signature
    expected = compute_signature(secret, payload)
    return hmac.compare_digest(expected, header)
