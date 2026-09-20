#!/usr/bin/env python3
"""Test helper: descendant ignores SIGTERM. MOCK only."""

from __future__ import annotations

import os
import signal
import time

pid_file = os.environ.get("STICKY_PID_FILE")
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if pid_file:
        with open(pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    while True:
        time.sleep(1)
if pid_file:
    with open(pid_file + ".parent", "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
time.sleep(60)
