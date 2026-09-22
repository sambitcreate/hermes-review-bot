#!/usr/bin/env python3
"""Create GitHub repository webhooks for Hermes Review Bot (used by setup.sh).

Fine-grained review tokens usually lack webhook (Administration) permission, so
this helper tries the API with the available token and PRINTS the exact manual
steps whenever automation fails — the manual path is also the README path.

Usage:
  github_webhook.py --url https://tunnel.example.com/webhooks/hermes-review-bot \\
                    --secret <hex> --repos owner/a,owner/b [--dry-run]

Token chain (same as the review runner): HERMES_PR_REVIEW_GITHUB_TOKEN ->
GITHUB_TOKEN -> GH_TOKEN, plus ~/.hermes/.env. Never prints token values.
Exit code 0 only when every repo has a working webhook (or --dry-run).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def token() -> str | None:
    load_env(Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")) / ".env")
    return (os.environ.get("HERMES_PR_REVIEW_GITHUB_TOKEN")
            or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))


def api(method: str, path: str, tok: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        data=json.dumps(body).encode("utf-8"),
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {tok}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Hermes-Review-Bot",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        return e.code, payload
    except Exception as e:  # network/DNS etc.
        return 0, {"message": str(e)}


def hook_payload(url: str, secret: str) -> dict:
    return {
        "name": "web",
        "active": True,
        "events": ["pull_request", "issue_comment"],
        "config": {"url": url, "content_type": "json", "secret": secret, "insecure_ssl": "0"},
    }


def manual_steps(url: str, secret: str, repos: list[str], reason: str) -> None:
    print(f"! automated webhook creation unavailable ({reason})")
    print("  create it manually per repo: GitHub -> repo -> Settings -> Webhooks -> Add webhook")
    for repo in repos:
        print(f"    {repo}:")
        print(f"      Payload URL : {url}")
        print(f"      Content type: application/json")
        print(f"      Secret      : {secret}")
        print("      Events      : \"Let me select individual events\" -> Pull requests, Issue comments")
        print("      Active      : ✓")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Public webhook URL (tunnel -> gateway /webhooks/<route>)")
    ap.add_argument("--secret", required=True, help="Shared secret (gateway verifies X-Hub-Signature-256 with it)")
    ap.add_argument("--repos", required=True, help="Comma-separated owner/name list")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be created; no network")
    args = ap.parse_args()

    repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    if not repos:
        print("no repos given", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps({"would_create": [{**hook_payload(args.url, args.secret), "repo": r} for r in repos]}, indent=2))
        return 0

    tok = token()
    if not tok:
        manual_steps(args.url, args.secret, repos, "no GitHub token in env or ~/.hermes/.env")
        return 1

    failures = 0
    for repo in repos:
        status, body = api("POST", f"/repos/{repo}/hooks", tok, hook_payload(args.url, args.secret))
        if 200 <= status < 300:
            print(f"ok: webhook created on {repo} (id {body.get('id')})")
        else:
            failures += 1
            manual_steps(args.url, args.secret, [repo], f"{repo}: HTTP {status} {body.get('message', '')}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
