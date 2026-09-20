"""Bounded ctxfilter + existing Jev MCP relevance classification."""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.1.0"

CTXFILTER_SRC = Path(
    "/Users/takamasa/Documents/Codex/2026-09-20/codex-context-filter-grok46/outputs/src"
)
if str(CTXFILTER_SRC) not in sys.path:
    sys.path.insert(0, str(CTXFILTER_SRC))
