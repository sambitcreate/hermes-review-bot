#!/usr/bin/env python3
"""Hermes Review Bot webhook route script (installed by setup.sh).

setup.sh copies this file to ~/.hermes/scripts/hermes-review-bot-handler.py —
the gateway only executes route scripts that are real files under that
directory (symlinks are rejected by _resolve_script_path).

Gateway contract (gateway/platforms/webhook_filters.run_route_script):
  * the payload arrives as JSON on stdin
  * stdout must be a JSON object; {"__hermes_ignore__": true} drops the
    webhook after the side effects (empty stdout or non-zero exit also drops
    it, with a gateway warning)
  * must exit quickly — the actual review runs DETACHED

Routing (every rule is re-validated inside scripts/review.py):
  * repository must be listed in config `repos:` — fail closed when unset
  * pull_request: action in config `actions:`; draft PRs skipped when
    `skip_drafts: true`
  * issue_comment: body is exactly `/hermes review` or `/hermes review full`
    and `followup_commands` is enabled

Config: ~/.hermes/review-bot/config.yaml (HERMES_REVIEW_BOT_CONFIG overrides).
`install_path` in that config points at this package's clone, where
scripts/review.py lives (written by setup.sh).

Set HERMES_REVIEW_HANDLER_DRY=1 to print the would-be spawn instead of
running it (used by the test suite; the gateway never sets this).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

IGNORE = {"__hermes_ignore__": True}
COMMANDS = {"/hermes review": "incremental", "/hermes review full": "full"}


def log(msg: str) -> None:
    print(f"hermes-review-bot handler: {msg}", file=sys.stderr, flush=True)


def load_config() -> dict:
    path = Path(os.environ.get("HERMES_REVIEW_BOT_CONFIG")
                or (Path.home() / ".hermes" / "review-bot" / "config.yaml"))
    if not path.exists():
        return {}
    try:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception as e:
        log(f"config parse failed ({path}): {e}")
        return {}


def emit(payload: dict, **extra) -> int:
    print(json.dumps({**payload, **extra}))
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        log(f"no/invalid payload on stdin: {e}")
        return emit(IGNORE)

    cfg = load_config()
    repos = [str(r).lower() for r in (cfg.get("repos") or [])]
    allowed_actions = {str(a) for a in (cfg.get("actions")
                                        or ["opened", "reopened", "synchronize", "ready_for_review"])}
    skip_drafts = bool(cfg.get("skip_drafts", True))
    followups = bool(cfg.get("followup_commands", True))

    repo = str(((payload.get("repository") or {}).get("full_name")) or "")
    if not repo:
        log("ignore: payload has no repository.full_name")
        return emit(IGNORE)
    if repo.lower() not in repos:
        log(f"ignore: repo {repo!r} not in configured repos ({repos or 'none'})")
        return emit(IGNORE)

    install = os.environ.get("HERMES_REVIEW_BOT_HOME") or cfg.get("install_path")
    if not install:
        log("install_path missing from config — re-run setup.sh")
        return emit(IGNORE)
    review_py = Path(str(install)) / "scripts" / "review.py"
    if not review_py.is_file():
        log(f"review runner not found at {review_py} — re-run setup.sh")
        return emit(IGNORE)

    pr = payload.get("pull_request")
    comment = payload.get("comment")
    issue = payload.get("issue") or {}

    if isinstance(pr, dict):
        action = str(payload.get("action") or "")
        if action not in allowed_actions:
            log(f"ignore: pull_request action {action!r} not in config actions")
            return emit(IGNORE)
        if skip_drafts and pr.get("draft"):
            log("ignore: draft PR (skip_drafts: true)")
            return emit(IGNORE)
        number = pr.get("number") or payload.get("number")
        if number is None:
            log("ignore: pull_request payload missing number")
            return emit(IGNORE)
        head_sha = str(((pr.get("head") or {}).get("sha")) or "")
        argv = [sys.executable, str(review_py), "--from-webhook",
                "--pull-request-number", str(number),
                "--payload-action", action,
                "--head-sha", head_sha]
        desc = f"pull_request:{action}:#{number}"
    elif isinstance(comment, dict) and issue.get("pull_request"):
        if not followups:
            log("ignore: followup_commands disabled")
            return emit(IGNORE)
        body = re.sub(r"\s+", " ", str(comment.get("body") or "").strip().lower())
        if body not in COMMANDS:
            log("ignore: comment is not a Hermes review command")
            return emit(IGNORE)
        number = issue.get("number")
        if number is None:
            log("ignore: issue_comment payload missing issue.number")
            return emit(IGNORE)
        argv = [sys.executable, str(review_py), "--from-webhook",
                "--issue-number", str(number),
                "--trigger-comment-id", str(comment.get("id") or ""),
                "--payload-action", str(payload.get("action") or "")]
        desc = f"issue_comment:{body!r}:#{number}"
    else:
        log("ignore: unhandled event shape (not pull_request or PR issue_comment)")
        return emit(IGNORE)

    env = os.environ.copy()
    env["HERMES_PR_REVIEW_REPO"] = repo  # canonical owner/name from the payload

    if os.environ.get("HERMES_REVIEW_HANDLER_DRY"):
        return emit(IGNORE, dry_spawn={"argv": argv, "repo": repo, "desc": desc})

    hermes_home = Path(env.get("HERMES_HOME") or (Path.home() / ".hermes"))
    log_dir = hermes_home / "logs" / "review-bot"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "webhook.log").open("a", encoding="utf-8") as out:
        subprocess.Popen(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    log(f"spawned detached review: {desc}")
    return emit(IGNORE)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        # Fail closed: never crash the gateway route, just drop this webhook.
        print(json.dumps(IGNORE))
        raise SystemExit(0)
