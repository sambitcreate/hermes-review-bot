"""Global config layering + BASE-branch trust for .hermes-review.yml."""
import subprocess

import pytest

GOOD_CONFIG = """
engine: hermes
model: openrouter/x
repos:
  - foo/bar
  - foo/baz
actions: [opened, synchronize]
skip_drafts: false
output_style: request_changes
followup_commands: false
timeout_minutes: 45
status_context: My Bot
prompt_file: /tmp/custom.md
poll:
  interval: 10m
  max_prs: 5
webhook:
  url: https://example.com/webhooks/r
  secret: abc
"""


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True,
                   env={"PATH": subprocess.run(["printenv", "PATH"], capture_output=True, text=True).stdout,
                        "HOME": __import__("os").environ.get("HOME", "/tmp")})


def test_global_config_defaults_when_missing(make_review):
    m = make_review()
    m.load_global_config()
    assert m.GCFG.engine == "agy"
    assert m.GCFG.repos == []
    assert m.GCFG.skip_drafts is True
    assert m.GCFG.output_style == "comment"
    assert m.GCFG.followup_commands is True
    assert m.STATUS_CONTEXT == "Hermes Review Bot"
    assert m.ALLOWED_ACTIONS == {"opened", "reopened", "synchronize", "ready_for_review"}
    assert m.INITIAL_REVIEW_ACTIONS == {"opened", "reopened", "ready_for_review"}  # synchronize excluded


def test_global_config_full_load(make_review):
    m = make_review(GOOD_CONFIG)
    m.load_global_config()
    g = m.GCFG
    assert g.engine == "hermes" and g.model == "openrouter/x"
    assert g.repos == ["foo/bar", "foo/baz"]
    assert g.skip_drafts is False
    assert g.output_style == "request_changes"
    assert g.followup_commands is False
    assert g.timeout_minutes == 45
    assert g.prompt_file == "/tmp/custom.md"
    assert g.poll_interval == "10m" and g.poll_max_prs == 5
    assert g.webhook_url.startswith("https://") and g.webhook_secret == "abc"
    assert m.STATUS_CONTEXT == "My Bot"
    assert m.FOLLOWUP_STATUS_CONTEXT == "My Bot (follow-up)"
    assert m.ALLOWED_ACTIONS == {"opened", "synchronize"}
    assert m.model_label() == "hermes/openrouter/x"


def test_bad_values_fail_loudly(make_review):
    m = make_review("engine: deepseek-r1\n")
    with pytest.raises(SystemExit, match="engine"):
        m.load_global_config()
    m = make_review("repos: [not-a-repo]\n")
    with pytest.raises(SystemExit, match="owner/name"):
        m.load_global_config()
    m = make_review("output_style: shouty\n")
    with pytest.raises(SystemExit, match="output_style"):
        m.load_global_config()
    m = make_review("engine: [broken, yaml\n")
    with pytest.raises(SystemExit, match="not valid YAML"):
        m.load_global_config()


def test_prompt_resolution_order(make_review, monkeypatch, tmp_path):
    m = make_review()
    assert m.prompt_path() == m.BUNDLE_DIR / "prompts" / "review-default.md"  # bundled default
    m.GCFG.prompt_file = str(tmp_path / "cfg.md")
    assert m.prompt_path() == tmp_path / "cfg.md"                              # config next
    monkeypatch.setenv("HERMES_PR_REVIEW_PROMPT", str(tmp_path / "env.md"))
    assert m.prompt_path() == tmp_path / "env.md"                              # env wins


def _run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _git_with_identity(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T", *args],
                   cwd=str(cwd), check=True, capture_output=True)


def test_repo_config_read_from_base_branch_not_pr_head(make_review, tmp_path):
    """A PR that loosens its own rules on the branch must not affect review of itself."""
    m = make_review()
    src = tmp_path / "src"
    src.mkdir()
    _run_git(src, "init", "-q", "-b", "main")
    cfg_a = "strictness: 1\nengine: hermes\nenabled: true\n"
    (src / ".hermes-review.yml").write_text(cfg_a)
    (src / ".hermes-review").mkdir()
    (src / ".hermes-review" / "rules.md").write_text("BASE RULES\n")
    _run_git(src, "add", "-A")
    _git_with_identity(src, "commit", "-q", "-m", "base")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True, capture_output=True)

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
    _run_git(work, "checkout", "-q", "-b", "feature")
    # PR head tries to loosen everything:
    cfg_b = "strictness: 3\nengine: claude\nenabled: false\n"
    (work / ".hermes-review.yml").write_text(cfg_b)
    (work / ".hermes-review" / "rules.md").write_text("HEAD RULES\n")
    _run_git(work, "add", "-A")
    _git_with_identity(work, "commit", "-q", "-m", "loosen rules on branch")

    loaded = m.load_repo_config(work, "main")
    assert loaded.source == ".hermes-review.yml"
    assert loaded.strictness == 1, "must use BASE branch strictness, not PR head"
    assert loaded.engine == "hermes", "must use BASE branch engine, not PR head"
    assert loaded.enabled is True, "enabled:false on the PR head must not disable review of that PR"
    rules = dict(loaded.context_docs)
    assert rules.get(".hermes-review/rules.md") == "BASE RULES"
    # Base branch has no context docs / engine invalid on head is ignored because head never read
    assert m._read_base_text(work, "main", "CLAUDE.md") is None


def test_repo_config_missing_file_gives_defaults(make_review, tmp_path):
    m = make_review()
    src = tmp_path / "s"
    src.mkdir()
    _run_git(src, "init", "-q", "-b", "main")
    (src / "README.md").write_text("hi\n")
    _run_git(src, "add", "-A")
    _git_with_identity(src, "commit", "-q", "-m", "base")
    bare = tmp_path / "o.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True, capture_output=True)
    work = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
    cfg = m.load_repo_config(work, "main")
    assert cfg.source == "defaults"
    assert cfg.strictness == 2 and cfg.enabled is True


def test_enabled_false_skips_via_config_skip_reason(make_review):
    m = make_review()
    reason = m.config_skip_reason(
        {"user": {"login": "x"}, "head": {"ref": "f"}, "labels": [], "title": "t", "body": "b"},
        [{"filename": "a.ts", "status": "modified", "additions": 1, "deletions": 0}],
        m.RepoConfig(enabled=False),
    )
    assert reason == "disabled via .hermes-review.yml"


def test_invalid_engine_in_repo_config_raises(make_review, tmp_path):
    m = make_review()
    src = tmp_path / "s"
    src.mkdir()
    _run_git(src, "init", "-q", "-b", "main")
    (src / ".hermes-review.yml").write_text("engine: deepseek\n")
    _run_git(src, "add", "-A")
    _git_with_identity(src, "commit", "-q", "-m", "base")
    bare = tmp_path / "o.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True, capture_output=True)
    work = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
    with pytest.raises(RuntimeError, match="unsupported"):
        m.load_repo_config(work, "main")
