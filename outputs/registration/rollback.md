# Rollback

Default is dry-run. Tests use temporary config fixtures. Do not apply globally until Codex review and sandbox permission.

```bash
/opt/homebrew/opt/python@3.14/bin/python3.14 /Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/outputs/registration/rollback-ctxfilter-jev.py
```

`--apply` removes only the exact owned `# BEGIN CTXFILTER-JEV MCP` / `<!-- BEGIN CTXFILTER-JEV -->` blocks. Modified or duplicate blocks stop without mutation. Jev, ctxfilter, jev-gate, and other content stay. Not a whole-file restore.

Installer backups, if apply is later authorized, go under `work/backup/` as 0600 files in a 0700 directory.
