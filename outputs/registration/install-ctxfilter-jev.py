#!/usr/bin/env python3
"""Insert owned ctxfilter-jev blocks. Default dry-run. Tests may pass fixture paths."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC = Path("/Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/outputs/src")
sys.path.insert(0, str(SRC))

from ctxfilter_jev.registration import (  # noqa: E402
    DEFAULT_AGENTS,
    DEFAULT_BACKUP,
    DEFAULT_CONFIG,
    RegistrationError,
    run_install,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--agents", default=str(DEFAULT_AGENTS))
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP))
    args = parser.parse_args()
    try:
        report = run_install(
            config_path=Path(args.config),
            agents_path=Path(args.agents),
            backup_dir=Path(args.backup_dir),
            apply=args.apply,
        )
    except RegistrationError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": exc.message, "extra": exc.extra}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
