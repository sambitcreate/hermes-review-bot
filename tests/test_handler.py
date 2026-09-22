"""Webhook handler routing against fixture payloads (dry-run spawn: no side effects)."""
import json
import subprocess
import sys
from pathlib import Path

HANDLER = Path(__file__).resolve().parent.parent / "scripts" / "webhook_handler.py"
INSTALL = str(Path(__file__).resolve().parent.parent)  # this repo has scripts/review.py

CONFIG = f"""
repos:
  - Foo/Bar
install_path: {INSTALL}
actions: [opened, reopened, synchronize, ready_for_review]
skip_drafts: true
followup_commands: true
"""


def run_handler(payload, config_text=CONFIG, dry=True, install_env=None, stdin_text=None):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config.yaml"
        cfg.write_text(config_text, encoding="utf-8")
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(Path.home()),
            "HERMES_REVIEW_BOT_CONFIG": str(cfg),
        }
        if dry:
            env["HERMES_REVIEW_HANDLER_DRY"] = "1"
        if install_env:
            env["HERMES_REVIEW_BOT_HOME"] = install_env
        data = stdin_text if stdin_text is not None else json.dumps(payload)
        proc = subprocess.run([sys.executable, str(HANDLER)], input=data,
                              capture_output=True, text=True, env=env, timeout=30)
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc, out


def pr_payload(action="opened", draft=False, repo="Foo/Bar", number=7, sha="a" * 40):
    return {
        "action": action,
        "number": number,
        "pull_request": {"number": number, "draft": draft, "head": {"sha": sha}},
        "repository": {"full_name": repo},
    }


def comment_payload(body="/hermes review", repo="Foo/Bar", number=7, cid=555):
    return {
        "action": "created",
        "issue": {"number": number, "pull_request": {"html_url": "https://x/1"}},
        "comment": {"id": cid, "body": body},
        "repository": {"full_name": repo},
    }


def test_pr_opened_spawns_review():
    proc, out = run_handler(pr_payload())
    assert proc.returncode == 0
    assert out["__hermes_ignore__"] is True
    argv = out["dry_spawn"]["argv"]
    assert "--from-webhook" in argv and "--pull-request-number" in argv
    assert argv[argv.index("--pull-request-number") + 1] == "7"
    assert argv[argv.index("--payload-action") + 1] == "opened"
    assert out["dry_spawn"]["repo"] == "Foo/Bar"


def test_unconfigured_repo_ignored():
    _, out = run_handler(pr_payload(repo="Someone/Else"))
    assert out["__hermes_ignore__"] is True and "dry_spawn" not in out


def test_disallowed_action_ignored():
    _, out = run_handler(pr_payload(action="labeled"))
    assert "dry_spawn" not in out


def test_draft_ignored_when_skip_drafts():
    _, out = run_handler(pr_payload(draft=True))
    assert "dry_spawn" not in out


def test_draft_allowed_when_configured():
    cfg = CONFIG.replace("skip_drafts: true", "skip_drafts: false")
    _, out = run_handler(pr_payload(draft=True), config_text=cfg)
    assert "dry_spawn" in out


def test_review_command_spawns_followup():
    _, out = run_handler(comment_payload(body="/hermes review full"))
    argv = out["dry_spawn"]["argv"]
    assert "--issue-number" in argv and "--trigger-comment-id" in argv
    assert argv[argv.index("--trigger-comment-id") + 1] == "555"


def test_plain_comment_ignored():
    _, out = run_handler(comment_payload(body="LGTM"))
    assert "dry_spawn" not in out


def test_followups_disabled_ignored():
    cfg = CONFIG.replace("followup_commands: true", "followup_commands: false")
    _, out = run_handler(comment_payload(), config_text=cfg)
    assert "dry_spawn" not in out


def test_missing_install_path_ignored():
    cfg = "\nrepos: [Foo/Bar]\n"
    _, out = run_handler(pr_payload(), config_text=cfg)
    assert "dry_spawn" not in out


def test_malformed_stdin_fails_closed():
    proc, out = run_handler(None, stdin_text="not-json{")
    assert proc.returncode == 0 and out["__hermes_ignore__"] is True


def test_always_emits_ignore_json():
    """Gateway contract: stdout must be a JSON object; we always drop after side effects."""
    for payload in (pr_payload(), comment_payload(), {"repository": {"full_name": "Foo/Bar"}}):
        _, out = run_handler(payload)
        assert out.get("__hermes_ignore__") is True
