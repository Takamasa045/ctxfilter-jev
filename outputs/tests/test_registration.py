from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from ctxfilter_jev.registration import (  # noqa: E402
    OWNED_AG_BLOCK,
    OWNED_CFG_BLOCK,
    RegistrationError,
    apply_pair,
    plan_install,
    plan_rollback,
    run_install,
    run_rollback,
    sha,
)


BASE_CFG = """# sample
[mcp_servers.jev]
command = "/npx"
args = ["-y", "--ignore-scripts", "jev-mcp@0.4.0"]

[mcp_servers.ctxfilter]
command = "/python"
args = ["mcp"]
"""
BASE_AGENTS = "# agents\nkeep me\n"


class RegistrationTests(unittest.TestCase):
    def test_plan_install_and_rollback_on_temp_text(self) -> None:
        new_cfg, new_agents = plan_install(BASE_CFG, BASE_AGENTS)
        self.assertIn("[mcp_servers.jev]", new_cfg)
        self.assertIn("[mcp_servers.ctxfilter]", new_cfg)
        self.assertIn(OWNED_CFG_BLOCK.strip(), new_cfg)
        self.assertIn(OWNED_AG_BLOCK.strip(), new_agents)
        rolled_cfg, rolled_agents = plan_rollback(new_cfg, new_agents)
        self.assertNotIn("[mcp_servers.ctxfilter_jev]", rolled_cfg)
        self.assertIn("[mcp_servers.jev]", rolled_cfg)
        self.assertNotIn("BEGIN CTXFILTER-JEV", rolled_agents)
        self.assertIn("keep me", rolled_agents)

    def test_duplicate_and_unowned_and_modified_blocks_refuse(self) -> None:
        with self.assertRaises(RegistrationError) as raised:
            plan_install(BASE_CFG + "\n[mcp_servers.ctxfilter_jev]\ncommand='x'\n", BASE_AGENTS)
        self.assertEqual(raised.exception.code, "unowned_table")
        doubled = BASE_CFG + "\n" + OWNED_CFG_BLOCK + "\n" + OWNED_CFG_BLOCK
        with self.assertRaises(RegistrationError) as raised:
            plan_install(doubled, BASE_AGENTS)
        self.assertEqual(raised.exception.code, "marker_conflict")
        mutated = BASE_CFG + "\n" + OWNED_CFG_BLOCK.replace("ctxfilter-jev", "other")
        with self.assertRaises(RegistrationError):
            plan_install(mutated, BASE_AGENTS)
        installed, agents = plan_install(BASE_CFG, BASE_AGENTS)
        mutated_installed = installed.replace("ctxfilter-jev", "tampered", 1)
        with self.assertRaises(RegistrationError) as raised:
            plan_rollback(mutated_installed, agents)
        self.assertEqual(raised.exception.code, "marker_conflict")

    def test_apply_atomic_on_temp_files_and_private_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            agents = Path(tmp) / "AGENTS.md"
            backup = Path(tmp) / "backup"
            cfg.write_text(BASE_CFG, encoding="utf-8")
            agents.write_text(BASE_AGENTS, encoding="utf-8")
            report = run_install(config_path=cfg, agents_path=agents, backup_dir=backup, apply=True)
            self.assertTrue(report["applied"])
            self.assertIn("[mcp_servers.ctxfilter_jev]", cfg.read_text(encoding="utf-8"))
            mode = stat.S_IMODE(backup.stat().st_mode)
            self.assertEqual(mode, 0o700)
            backups = list(backup.iterdir())
            self.assertTrue(backups)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
            rolled = run_rollback(config_path=cfg, agents_path=agents, backup_dir=backup, apply=True)
            self.assertTrue(rolled["applied"])
            self.assertNotIn("[mcp_servers.ctxfilter_jev]", cfg.read_text(encoding="utf-8"))
            self.assertIn("[mcp_servers.jev]", cfg.read_text(encoding="utf-8"))

    def test_concurrent_update_not_clobbered_on_second_file_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            agents = Path(tmp) / "AGENTS.md"
            backup = Path(tmp) / "backup"
            cfg.write_text(BASE_CFG, encoding="utf-8")
            agents.write_text(BASE_AGENTS, encoding="utf-8")
            new_cfg, new_agents = plan_install(BASE_CFG, BASE_AGENTS)

            def after() -> None:
                cfg.write_text("unrelated-concurrent-edit\n", encoding="utf-8")
                agents.unlink()
                agents.mkdir()

            with self.assertRaises(RegistrationError) as raised:
                apply_pair(
                    cfg,
                    agents,
                    new_cfg,
                    new_agents,
                    sha(BASE_CFG),
                    sha(BASE_AGENTS),
                    backup,
                    after_config=after,
                )
            self.assertEqual(raised.exception.code, "partial_failure")
            self.assertTrue(raised.exception.extra.get("config_left_intact"))
            self.assertEqual(cfg.read_text(encoding="utf-8"), "unrelated-concurrent-edit\n")
            self.assertIn("config_backup", raised.exception.extra)

    def test_restore_only_when_config_still_exactly_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            agents = Path(tmp) / "AGENTS.md"
            backup = Path(tmp) / "backup"
            cfg.write_text(BASE_CFG, encoding="utf-8")
            agents.write_text(BASE_AGENTS, encoding="utf-8")
            new_cfg, new_agents = plan_install(BASE_CFG, BASE_AGENTS)

            def after() -> None:
                agents.unlink()
                agents.mkdir()

            with self.assertRaises(RegistrationError) as raised:
                apply_pair(
                    cfg,
                    agents,
                    new_cfg,
                    new_agents,
                    sha(BASE_CFG),
                    sha(BASE_AGENTS),
                    backup,
                    after_config=after,
                )
            self.assertEqual(raised.exception.code, "partial_failure")
            self.assertTrue(raised.exception.extra.get("config_restored"))
            self.assertEqual(cfg.read_text(encoding="utf-8"), BASE_CFG)

    def test_conflict_stop_when_preimage_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            agents = Path(tmp) / "AGENTS.md"
            backup = Path(tmp) / "backup"
            cfg.write_text(BASE_CFG, encoding="utf-8")
            agents.write_text(BASE_AGENTS, encoding="utf-8")
            from ctxfilter_jev.registration import atomic_write

            with self.assertRaises(Exception):
                atomic_write(cfg, "nope\n", sha("other"))
            self.assertEqual(cfg.read_text(encoding="utf-8"), BASE_CFG)
