#!/usr/bin/env bash
# Hermes Review Bot — installer + doctor.
#
#   ./setup.sh                 interactive install (config + launchers + trigger)
#   ./setup.sh --check         READ-ONLY preflight/doctor; non-zero exit on failure
#
# Flags:
#   --engine  NAME     agy | claude | codex | opencode | gemini | hermes
#   --repos   a/b,c/d  repositories the bot serves
#   --model   VALUE    engine model (verified against `agy models` for agy)
#   --mode    MODE     poll (default) | webhook | both
#   --interval SCHED   polling schedule for `hermes cron` (default 5m)
#   --yes              non-interactive: never prompt, never install engines
#   --force            overwrite an existing config (backup kept)
#
# This script never reads, writes, or prints credential values. It only checks
# that credentials EXIST and works. Auth itself stays manual (see README).
set -u

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
HH="${HERMES_HOME:-$HOME/.hermes}"
CFG_DIR="$HH/review-bot"
CFG="$CFG_DIR/config.yaml"
SCRIPTS_DIR="$HH/scripts"
TOKEN_ENV_NAMES=(HERMES_PR_REVIEW_GITHUB_TOKEN GITHUB_TOKEN GH_TOKEN)

ENGINE="" MODEL="" REPOS="" MODE="poll" INTERVAL="" YES=0 FORCE=0 CHECK=0
PASS=0; FAILN=0; WARNS=0

say()  { printf '%s\n' "$*"; }
ok()   { PASS=$((PASS+1));   printf '  [ OK ] %s\n' "$*"; }
bad()  { FAILN=$((FAILN+1)); printf '  [FAIL] %s\n' "$*"; }
warn() { WARNS=$((WARNS+1)); printf '  [WARN] %s\n' "$*"; }
hr()   { printf -- '----------------------------------------\n'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --check)    CHECK=1 ;;
    --engine)   ENGINE="${2:-}"; shift ;;
    --repos)    REPOS="${2:-}"; shift ;;
    --model)    MODEL="${2:-}"; shift ;;
    --mode)     MODE="${2:-}"; shift ;;
    --interval) INTERVAL="${2:-}"; shift ;;
    --yes)      YES=1 ;;
    --force)    FORCE=1 ;;
    -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
    *) say "unknown flag: $1 (see --help)"; exit 2 ;;
  esac
  shift
done

case "$MODE" in poll|webhook|both) ;; *) say "--mode must be poll|webhook|both"; exit 2 ;; esac

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

engine_binary_path() {
  # mirrors review.py: PATH first, then ~/.local/bin
  local name="$1" found
  found="$(command -v "$name" 2>/dev/null || true)"
  if [ -z "$found" ] && [ -x "$HOME/.local/bin/$name" ]; then found="$HOME/.local/bin/$name"; fi
  printf '%s' "$found"
}

read_cfg_field() {
  # read_cfg_field <python-expr on loaded dict d>  — value printed or empty
  python3 - "$CFG" "$1" <<'PYEOF' 2>/dev/null || true
import sys, yaml
try:
    d = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
    v = eval(sys.argv[2], {"d": d})
    print(v if v is not None else "", end="")
except Exception:
    pass
PYEOF
}

set_cfg_line() {
  # set_cfg_line <key> <value>  — text-level replace, comments preserved
  python3 - "$CFG" "$1" "$2" <<'PYEOF'
import re, sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()
line = f'{key}: "{value}"' if value != "" or key in ("install_path", "prompt_file") else f"{key}:"
if re.search(rf"(?m)^{re.escape(key)}:.*$", text):
    text = re.sub(rf"(?m)^{re.escape(key)}:.*$", line, text, count=1)
else:
    text = text.rstrip("\n") + "\n" + line + "\n"
open(path, "w", encoding="utf-8").write(text)
PYEOF
}

# --------------------------------------------------------------------------
# CHECKS (all read-only)
# --------------------------------------------------------------------------
check_tools() {
  hr; say "Tooling"; hr
  if have python3; then
    if python3 -c "import yaml" 2>/dev/null; then ok "python3 + PyYAML ($(python3 -V 2>&1))"
    else bad "python3 PyYAML missing — install with: python3 -m pip install --user pyyaml"; fi
  else bad "python3 not found"; fi
  if have git; then ok "git $(git --version | awk '{print $3}')"; else bad "git not found"; fi
  if have hermes; then ok "hermes CLI ($(command -v hermes))"; else
    bad "hermes CLI not found — this bot runs ON your Hermes; install Hermes first (see hermes-agent docs)"; fi
  if have gh; then ok "gh CLI (optional — used for automated webhook creation)"; else
    warn "gh CLI not installed (optional; webhook creation falls back to printed manual steps)"; fi
}

check_token() {
  hr; say "GitHub token"; hr
  python3 - "${TOKEN_ENV_NAMES[@]}" <<'PYEOF' && return 0 || return 1
import json, os, sys, urllib.request
from pathlib import Path

hh = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
env_file = hh / ".env"
found_name = None
if env_file.exists():
    for line in env_file.read_text(errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k = line.split("=", 1)[0].strip()
            if k in sys.argv[1:]:
                found_name = k
if not found_name:
    for k in sys.argv[1:]:
        if os.environ.get(k):
            found_name = k
            break
if not found_name:
    print("  [FAIL] no GitHub token found. Add one of %s to %s" % ("/".join(sys.argv[1:]), env_file))
    print("         fine-grained PAT scopes: Contents:read  Pull requests:read,write  Issues:read,write  Statuses:write  Metadata:read")
    sys.exit(1)

# load the value WITHOUT printing it, then verify against the API
if env_file.exists():
    for line in env_file.read_text(errors="ignore").splitlines():
        line = line.strip()
        if line.startswith(found_name + "="):
            os.environ.setdefault(found_name, line.split("=", 1)[1].strip().strip('"').strip("'"))
tok = os.environ.get(found_name)

def api(path):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {tok}",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Hermes-Review-Bot"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode() or "{}")

try:
    me = api("/user")
except Exception as e:
    print(f"  [FAIL] token from {found_name} rejected by GitHub API: {e}")
    sys.exit(1)
print(f"  [ OK ] token accepted ({found_name}) as @{me.get('login')}")

repos = []
cfg = None
try:
    cfg = yaml_safe = __import__("yaml").safe_load(open(os.environ.get("HRB_CFG", ""), encoding="utf-8")) or {}
    repos = [str(r) for r in (cfg.get("repos") or [])]
except Exception:
    pass
for repo in repos[:5]:
    try:
        info = api(f"/repos/{repo}")
        perm = (info.get("permissions") or {})
        can_push = bool(perm.get("push"))
        print(f"  [ OK ] {repo}: visible, push={'yes' if can_push else 'NO — bot needs write access to post reviews'}")
    except Exception as e:
        print(f"  [FAIL] {repo}: {e}")
        sys.exit(1)
sys.exit(0)
PYEOF
}

check_engine() {
  hr; say "Engine: ${ENGINE:-<from config>}"; hr
  local eng="$1" model="$2" bin
  bin="$(engine_binary_path "$eng")"
  if [ -z "$bin" ]; then
    bad "engine '$eng' binary not found (PATH or ~/.local/bin). Install command: $(install_cmd "$eng")"
    return
  fi
  ok "binary: $bin"

  # deep, engine-specific checks (auth is reported, never modified)
  case "$eng" in
    agy)
      if [ -f "$HOME/.gemini/antigravity-cli/settings.json" ]; then ok "agy sign-in settings present"
      elif grep -qs "^GEMINI_API_KEY=" "$HH/.env" || [ -n "${GEMINI_API_KEY:-}" ]; then ok "GEMINI_API_KEY present (headless mode)"
      else warn "agy has no sign-in and no GEMINI_API_KEY — run 'agy' once to sign in (or add GEMINI_API_KEY to $HH/.env)"; fi
      if [ -n "$model" ]; then
        if agy models 2>/dev/null | grep -qF "$model"; then ok "model available: $model"
        else bad "model '$model' not offered by 'agy models' — fix model: in $CFG"; fi
      fi
      ;;
    opencode)
      if [ -f "$HOME/.local/share/opencode/auth.json" ] || [ -f "$HOME/.config/opencode/auth.json" ]; then
        ok "opencode auth.json present"
      else warn "opencode auth not found — run 'opencode auth login'"; fi
      ;;
    claude)
      if [ -f "$HOME/.claude.json" ] || [ -n "${ANTHROPIC_API_KEY:-}" ]; then ok "claude auth marker present"
      else warn "cannot verify claude auth — run 'claude -p \"reply OK\"' once to confirm"; fi
      ;;
    codex)
      if [ -d "$HOME/.codex" ]; then ok "~/.codex present"
      else warn "cannot verify codex auth — run 'codex login' once"; fi
      ;;
    gemini)
      if [ -f "$HOME/.gemini/oauth_creds.json" ] || grep -qs "^GEMINI_API_KEY=" "$HH/.env"; then ok "gemini auth marker present"
      else warn "cannot verify gemini auth — run 'gemini' once"; fi
      ;;
    hermes)
      ok "hermes engine uses your existing Hermes providers (no extra auth)"
      ;;
  esac
}

install_cmd() {
  case "$1" in
    agy)     say "curl -fsSL https://antigravity.google/cli/install.sh | bash" ;;
    claude)  say "npm install -g @anthropic-ai/claude-code" ;;
    codex)   say "npm install -g @openai/codex" ;;
    opencode) say "curl -fsSL https://opencode.ai/install | bash" ;;
    gemini)  say "npm install -g @google/gemini-cli" ;;
    hermes)  say "(hermes should already be installed — see hermes-agent docs)" ;;
    *) say "" ;;
  esac
}

check_prompt() {
  hr; say "Prompt"; hr
  local pf
  pf="$(read_cfg_field 'd.get("prompt_file")')"
  if [ -n "$pf" ]; then
    if [ -f "$pf" ]; then ok "custom prompt: $pf"; else bad "prompt_file set but missing: $pf"; fi
  else
    if [ -f "$REPO_ROOT/prompts/review-default.md" ]; then ok "bundled default prompt present"
    else bad "bundled prompt missing from clone: $REPO_ROOT/prompts/review-default.md"; fi
  fi
}

check_trigger() {
  hr; say "Trigger"; hr
  if [ "$MODE" = "poll" ] || [ "$MODE" = "both" ]; then
    if have hermes && hermes cron list 2>/dev/null | grep -q "hermes-review-bot"; then
      ok "polling cron job installed (hermes cron list → hermes-review-bot)"
    elif [ "$CHECK" = "1" ] && [ "$INSTALLING" = "0" ]; then
      warn "polling cron job not installed yet — run ./setup.sh"
    else bad "polling cron job missing"; fi
  fi
  if [ "$MODE" = "webhook" ] || [ "$MODE" = "both" ]; then
    if have hermes && hermes webhook list 2>/dev/null | grep -q "hermes-review-bot"; then
      ok "webhook route installed (hermes webhook list → hermes-review-bot)"
    elif [ "$CHECK" = "1" ] && [ "$INSTALLING" = "0" ]; then
      warn "webhook route not installed yet — run ./setup.sh --mode webhook"
    else bad "webhook route missing"; fi
    if curl -fsS -m 3 http://127.0.0.1:8644/health >/dev/null 2>&1; then
      ok "gateway healthy (127.0.0.1:8644)"
    else
      warn "gateway not answering on 127.0.0.1:8644 — start it: hermes gateway (required for webhook mode)"
    fi
    if [ -n "$(read_cfg_field 'd.get("webhook", {}).get("url")')" ]; then ok "webhook.url set in config"
    else warn "webhook.url empty in config — needed before GitHub can deliver events"; fi
  fi
  if [ "$MODE" = "poll" ]; then
    if have hermes && hermes cron status 2>/dev/null | grep -qi "running"; then ok "cron scheduler running"
    else warn "hermes cron scheduler not running — start it or polls never fire (hermes cron status)"; fi
  fi
}

run_checks() {
  INSTALLING=0
  say ""; say "Hermes Review Bot — preflight (read-only)"; hr
  check_tools
  if [ ! -f "$CFG" ]; then
    hr; say "Config"; hr
    warn "no config at $CFG yet — run ./setup.sh to create it"
    ENGINE="${ENGINE:-agy}"; MODEL="${MODEL:-}"
  else
    hr; say "Config"; hr
    ok "$CFG present"
    if python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" "$CFG" 2>/dev/null; then
      ok "config parses as YAML"
      ENGINE="${ENGINE:-$(read_cfg_field 'd.get("engine")')}"
      MODEL="${MODEL:-$(read_cfg_field 'd.get("model")')}"
      REPOS="${REPOS:-$(read_cfg_field '",".join(d.get("repos") or [])')}"
      export HRB_CFG="$CFG"
    else bad "config is not valid YAML: $CFG"; fi
  fi
  [ -n "$REPOS" ] && ok "repos: $REPOS" || warn "no repos configured"
  export HRB_CFG="$CFG"
  if ! check_token; then FAILN=$((FAILN+1)); fi
  check_engine "${ENGINE:-agy}" "${MODEL:-}"
  check_prompt
  check_trigger
  hr
  say "Result: $PASS ok, $FAILN failed, $WARNS warnings"
  [ "$FAILN" -eq 0 ] || exit 1
}

# --------------------------------------------------------------------------
# INSTALL
# --------------------------------------------------------------------------
offer_engine_install() {
  local eng="$1" cmd
  [ "$YES" = "1" ] && return 1
  [ -t 0 ] || return 1
  cmd="$(install_cmd "$eng")"
  printf "Engine '%s' is not installed. Install now with:\n  %s\nRun it now? [y/N] " "$eng" "$cmd"
  read -r answer
  case "$answer" in y|Y|yes) say "running: $cmd"; sh -c "$cmd" && return 0 ;; esac
  return 1
}

write_config() {
  mkdir -p "$CFG_DIR"
  if [ -f "$CFG" ] && [ "$FORCE" != "1" ]; then
    say "config exists ($CFG) — keeping it (use --force to overwrite, backup is made)"
  else
    [ -f "$CFG" ] && cp "$CFG" "$CFG.bak.$(date +%Y%m%d%H%M%S)" && say "backed up existing config"
    cp "$REPO_ROOT/templates/config.example.yaml" "$CFG"
    say "created $CFG from template"
  fi
  # always refresh machine-specific fields (idempotent)
  set_cfg_line install_path "$REPO_ROOT"
  if [ -n "$ENGINE" ]; then python3 - "$CFG" "$ENGINE" <<'PYEOF'
import re, sys
path, eng = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = re.sub(r"(?m)^engine:.*$", f"engine: {eng}", text, count=1)
open(path, "w", encoding="utf-8").write(text)
PYEOF
  fi
  if [ -n "$MODEL" ]; then set_cfg_line model "$MODEL"; fi
  if grep -q "OWNER/EXAMPLE_REPO" "$CFG" 2>/dev/null; then
    bad "no repos configured — re-run with --repos owner/repo[,owner/repo]"
  fi
  if [ -n "$REPOS" ] && { [ "$FORCE" = "1" ] || ! grep -q "OWNER/EXAMPLE_REPO" "$CFG"; }; then
    python3 - "$CFG" "$REPOS" <<'PYEOF'
import re, sys
path, repos = sys.argv[1], [r.strip() for r in sys.argv[2].split(",") if r.strip()]
text = open(path, encoding="utf-8").read()
lines = "\n".join(f"  - {r}" for r in repos)
text = re.sub(r"(?m)^repos:\n(  - .*\n)+", f"repos:\n{lines}\n", text, count=1)
open(path, "w", encoding="utf-8").write(text)
PYEOF
    say "repos -> $REPOS"
  fi
}

install_launchers() {
  mkdir -p "$SCRIPTS_DIR"
  cp "$REPO_ROOT/scripts/webhook_handler.py" "$SCRIPTS_DIR/hermes-review-bot-handler.py"
  cp "$REPO_ROOT/scripts/hermes-review-bot-poll.py" "$SCRIPTS_DIR/hermes-review-bot-poll.py"
  chmod +x "$SCRIPTS_DIR/hermes-review-bot-handler.py" "$SCRIPTS_DIR/hermes-review-bot-poll.py" 2>/dev/null || true
  say "launchers installed into $SCRIPTS_DIR (gateway/cron require real files there)"
}

setup_polling() {
  local interval="${INTERVAL:-$(read_cfg_field 'd.get("poll", {}).get("interval")')}"
  interval="${interval:-5m}"
  if hermes cron list 2>/dev/null | grep -q "hermes-review-bot"; then
    say "polling cron already installed (schedule $interval) — edit with: hermes cron edit"
  else
    hermes cron create "$interval" --name hermes-review-bot \
      --script "$SCRIPTS_DIR/hermes-review-bot-poll.py" --no-agent
    say "polling cron installed: every $interval → scripts/review.py --poll (silent when healthy)"
  fi
}

setup_webhook() {
  if ! curl -fsS -m 3 http://127.0.0.1:8644/health >/dev/null 2>&1; then
    bad "gateway not healthy — start it (hermes gateway), then re-run ./setup.sh --mode webhook"
    return
  fi
  local secret url
  secret="$(openssl rand -hex 32)"
  url="$(read_cfg_field 'd.get("webhook", {}).get("url")')"
  hermes webhook subscribe hermes-review-bot \
    --events pull_request,issue_comment \
    --script "$SCRIPTS_DIR/hermes-review-bot-handler.py" \
    --secret "$secret" >/dev/null
  python3 - "$CFG" "$secret" <<'PYEOF'
import re, sys
path, secret = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = re.sub(r'(?m)^(\s*secret):.*$', rf'\1: "{secret}"', text, count=1)
open(path, "w", encoding="utf-8").write(text)
PYEOF
  ok "gateway route installed (hermes webhook subscribe hermes-review-bot) — HMAC enforced by the gateway"

  url="${url:-http://127.0.0.1:8644/webhooks/hermes-review-bot}"
  if [ -z "$url" ]; then url="http://127.0.0.1:8644/webhooks/hermes-review-bot"; fi
  say ""
  say "GitHub must now deliver events to:"
  say "  Payload URL : $url   (point a public tunnel here — see README 'Instant mode')"
  say "  Content type: application/json"
  say "  Secret      : (written into $CFG as webhook.secret)"
  say ""
  if [ -n "$REPOS" ]; then
    if [ "$YES" = "1" ]; then
      python3 "$REPO_ROOT/scripts/github_webhook.py" --url "$url" --secret "$secret" --repos "$REPOS" || true
    elif [ -t 0 ]; then
      printf "Create the GitHub webhooks now via API (needs a token with Administration:write)? [y/N] "
      read -r answer
      case "$answer" in y|Y|yes)
        python3 "$REPO_ROOT/scripts/github_webhook.py" --url "$url" --secret "$secret" --repos "$REPOS" || true ;;
      *) say "skipped — manual steps are printed by: python3 scripts/github_webhook.py --url URL --secret SECRET --repos $REPOS" ;;
      esac
    fi
  fi
}

install_all() {
  INSTALLING=1
  say "Hermes Review Bot — setup"; hr
  # 1. tools
  check_tools
  [ "$FAILN" -gt 0 ] && { say "tooling failures — fix these first"; exit 1; }

  # 2. config questions (interactive fill when values missing)
  if [ -z "$ENGINE" ] && [ -t 0 ] && [ "$YES" != "1" ]; then
    printf "Review engine [agy]: "; read -r ENGINE; ENGINE="${ENGINE:-agy}"
  fi
  ENGINE="${ENGINE:-agy}"
  if [ -z "$REPOS" ] && [ -t 0 ] && [ "$YES" != "1" ]; then
    printf "Repositories to review (owner/repo[,owner/repo...]): "; read -r REPOS
  fi
  if [ -z "$MODEL" ] && [ "$ENGINE" = "agy" ] && [ -t 0 ] && [ "$YES" != "1" ]; then
    printf "Model for agy [gemini-3.8-flash-high]: "; read -r MODEL; MODEL="${MODEL:-gemini-3.8-flash-high}"
  fi

  # 3. engine binary (offer install, confirm; auth stays manual)
  if [ -z "$(engine_binary_path "$ENGINE")" ]; then
    if offer_engine_install "$ENGINE"; then :; else
      bad "engine '$ENGINE' not installed — install it, then re-run setup.sh"
      say "  install: $(install_cmd "$ENGINE")"
      exit 1
    fi
  fi

  # 4. config + launchers
  write_config
  install_launchers

  # 5. trigger
  case "$MODE" in
    poll)    setup_polling ;;
    webhook) setup_webhook ;;
    both)    setup_polling; setup_webhook ;;
  esac

  # 6. final verification (same doctor, now expected green)
  say ""; say "Verifying install…"
  ENGINE="$ENGINE" MODEL="$MODEL" REPOS="$REPOS" run_checks || true

  say ""
  say "Next steps:"
  say "  1. Confirm token + engine auth (see warnings above; auth is manual, never automated)."
  say "  2. Open a draft PR on a configured repo — the bot reviews it within the poll interval."
  say "  3. Comment '/hermes review' on any reviewed PR for a follow-up round."
  say "Logs: $HH/logs/review-bot/   State: $HH/data/review-bot/"
}

if [ "$CHECK" = "1" ]; then
  run_checks
else
  install_all
fi
