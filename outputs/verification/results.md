# Final local verification

| Check | Exit | Count |
| --- | --- | --- |
| unittest discover outputs/tests | 0 | 41 OK, 3.291s, original 37 included |
| comparison --mock | 0 | mock only |
| comparison live reuse | 0 | 0 new Jev |
| install --apply | 0 | owned blocks only |
| rollback (no --apply) | 0 | dry-run |
| codex mcp get ctxfilter_jev | 0 | config readback |
| mcp_smoke.py | 0 | after hung-notification fix |

No new Jev inference.
