"""Drop cosmetic native-library noise from stderr (e.g. HTML-parser warnings).

Some compiled dependencies (the HTML extractor, TLS stack) print harmless lines
like 'foster parenting not implemented' or 'TLS alert warning ... UnrecognisedName'
straight to the OS stderr, bypassing Python. We splice a filter onto fd 2: a daemon
thread reads everything written to stderr, drops the known-noise lines, and forwards
the rest to the real stderr. Concurrency-safe (single reader), keeps tracebacks.
"""
from __future__ import annotations

import os
import threading

_NOISE = (
    "foster parenting not implemented",
    "TLS alert warning",
    "UnrecognisedName",
)

_installed = False


def install_stderr_filter(extra_patterns: tuple[str, ...] = ()) -> None:
    global _installed
    if _installed:
        return
    try:
        read_fd, write_fd = os.pipe()
        real_stderr = os.dup(2)
        os.dup2(write_fd, 2)
        os.close(write_fd)
    except OSError:
        return  # platform without dup2 support — leave stderr alone

    patterns = _NOISE + tuple(extra_patterns)

    def _pump() -> None:
        try:
            with os.fdopen(read_fd, "r", errors="replace") as src, os.fdopen(
                real_stderr, "w", errors="replace"
            ) as dst:
                for line in src:
                    if not any(p in line for p in patterns):
                        dst.write(line)
                        dst.flush()
        except Exception:
            pass

    threading.Thread(target=_pump, name="stderr-filter", daemon=True).start()
    _installed = True
