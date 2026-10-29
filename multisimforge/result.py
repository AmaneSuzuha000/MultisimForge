"""One result vocabulary for every command.

Every CLI command, and therefore every MCP tool, returns the same envelope:

    {"status": "PASS" | "WARN" | "FAIL" | "ERROR" | "CANCELLED",
     "exit_code": 0 | 1 | 2 | 130,
     ...command specific fields...}

PASS/WARN exit 0, FAIL (the design is wrong) exits 2, ERROR (the tool or the
environment failed) exits 1, CANCELLED exits 130. Nothing may report success
while its exit code says otherwise -- that rule is what this module exists for.
"""
from __future__ import annotations

import os
import secrets
import time
from enum import Enum


class Status(str, Enum):
    PASS = "PASS"          # all mandatory checks satisfied
    WARN = "WARN"          # usable, but with a declared, non-blocking caveat
    FAIL = "FAIL"          # a mandatory condition is not satisfied (bad design)
    ERROR = "ERROR"        # the tool or the environment failed
    CANCELLED = "CANCELLED"


EXIT_CODES = {Status.PASS: 0, Status.WARN: 0, Status.FAIL: 2, Status.ERROR: 1, Status.CANCELLED: 130}
_SEVERITY = {Status.PASS: 0, Status.WARN: 1, Status.FAIL: 2, Status.ERROR: 3, Status.CANCELLED: 4}


def exit_code_for(status) -> int:
    return EXIT_CODES[Status(status)]


def worst(statuses) -> Status:
    """The most severe status of an iterable (PASS when empty)."""
    out = Status.PASS
    for s in statuses:
        s = Status(s)
        if _SEVERITY[s] > _SEVERITY[out]:
            out = s
    return out


def envelope(payload: dict, status=Status.PASS, **extra) -> dict:
    """Attach status and exit_code to a command payload."""
    status = Status(status)
    out = dict(payload)
    out["status"] = status.value
    out["exit_code"] = exit_code_for(status)
    for key, value in extra.items():
        out.setdefault(key, value)
    return out


def new_run_id() -> str:
    """Sortable, collision-free id for one build/sim/capture run."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def env_run_id() -> str | None:
    return os.environ.get("MULTISIMFORGE_RUN_ID") or None
