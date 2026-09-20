"""Record config/AGENTS hashes and table presence. Does not print file bodies or keys."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SRC = Path("/Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/outputs/src")
sys.path.insert(0, str(SRC))

from ctxfilter_jev.registration import AG_BEGIN, CFG_BEGIN, plan_rollback  # noqa: E402

CONFIG = Path("/Users/takamasa/.codex/config.toml")
AGENTS = Path("/Users/takamasa/.codex/AGENTS.md")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot() -> dict:
    cfg = CONFIG.read_text(encoding="utf-8")
    agents = AGENTS.read_text(encoding="utf-8")
    rolled_cfg, rolled_agents = plan_rollback(cfg, agents)
    return {
        "config_sha": sha(CONFIG),
        "agents_sha": sha(AGENTS),
        "has_jev": "[mcp_servers.jev]" in cfg,
        "has_ctxfilter": "[mcp_servers.ctxfilter]" in cfg,
        "has_ctxfilter_jev_table": "[mcp_servers.ctxfilter_jev]" in cfg,
        "has_owned_cfg_marker": CFG_BEGIN in cfg,
        "has_owned_agents_marker": AG_BEGIN in agents,
        "rollback_cfg_sha": hashlib.sha256(rolled_cfg.encode()).hexdigest(),
        "rollback_agents_sha": hashlib.sha256(rolled_agents.encode()).hexdigest(),
        "config_bytes": CONFIG.stat().st_size,
        "agents_bytes": AGENTS.stat().st_size,
    }


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    print(json.dumps({"label": label, **snapshot()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
