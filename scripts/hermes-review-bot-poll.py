#!/usr/bin/env python3
"""Hermes Review Bot polling tick (installed by setup.sh).

setup.sh copies this file to ~/.hermes/scripts/hermes-review-bot-poll.py —
`hermes cron create --script` only accepts scripts under ~/.hermes/scripts/.

Behavior designed for `hermes cron create --no-agent`:
  * success -> stdout stays EMPTY (empty stdout = silent; the log lines went
    to logs/review-bot/poll.log instead)
  * failure -> one stdout line, so a configured --failure-deliver/--deliver
    target gets pinged
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    hermes_home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    cfg_path = Path(os.environ.get("HERMES_REVIEW_BOT_CONFIG")
                    or (hermes_home / "review-bot" / "config.yaml"))
    install = os.environ.get("HERMES_REVIEW_BOT_HOME")
    if not install and cfg_path.exists():
        try:
            import yaml
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            install = str((raw or {}).get("install_path") or "") or None
        except Exception as e:
            print(f"hermes-review-bot: config parse failed ({cfg_path}): {e}")
            return 1
    if not install:
        print("hermes-review-bot: install_path not set in config — re-run setup.sh")
        return 1
    review_py = Path(install) / "scripts" / "review.py"
    if not review_py.is_file():
        print(f"hermes-review-bot: runner not found at {review_py} — re-run setup.sh")
        return 1

    log_dir = hermes_home / "logs" / "review-bot"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "poll.log").open("a", encoding="utf-8") as out:
        rc = subprocess.run(
            [sys.executable, str(review_py), "--poll"],
            stdout=out,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        ).returncode
    if rc != 0:
        print(f"hermes-review-bot: poll failed (exit {rc}) — see {log_dir / 'poll.log'}")
        return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
