"""Observe existing requests and release results without making status requests."""

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from threading import Lock
from typing import Literal


@dataclass(frozen=True)
class GitHubStatus:
    remaining: int | None = None
    limit: int | None = None
    reset_at: float = 0
    source: str = ""
    repo: str = ""
    checked_at: float = 0


# Enough observed requests left that a few overlapping release checks cannot exhaust them.
PLENTIFUL_QUOTA = 30

_status = GitHubStatus()
_lock = Lock()


def quota_plentiful() -> bool:
    with _lock:
        status = _status
    return (
        status.remaining is not None
        and status.reset_at > time.time()
        and status.remaining >= PLENTIFUL_QUOTA
    )


def allowance() -> tuple[int, int] | None:
    """Return the last reported (remaining, limit) while its window is current."""
    with _lock:
        status = _status
    if status.remaining is None or status.limit is None or status.reset_at <= time.time():
        return None
    return status.remaining, status.limit


def quota_low() -> bool:
    """True only when GitHub last reported fewer requests than a scheduled check should use."""
    with _lock:
        status = _status
    return (
        status.remaining is not None
        and status.reset_at > time.time()
        and status.remaining < PLENTIFUL_QUOTA
    )


def observe_headers(headers: Mapping[str, str]) -> None:
    global _status
    values = {name.lower(): value for name, value in headers.items()}
    try:
        remaining = int(values["x-ratelimit-remaining"])
        limit = int(values["x-ratelimit-limit"])
        reset_at = int(values["x-ratelimit-reset"])
    except (KeyError, TypeError, ValueError):
        return
    if not (0 <= remaining <= limit <= 10**9 and 0 < reset_at <= time.time() + 86400):
        return
    with _lock:
        _status = replace(_status, remaining=remaining, limit=limit, reset_at=reset_at)


def release_result(
    source: Literal[
        "GitHub API",
        "GitHub release page",
        "GitHub release feed",
        "Public GitHub pages",
        "Cached information",
    ],
    repo: str,
    checked_at: float,
) -> None:
    global _status
    with _lock:
        _status = replace(_status, source=source, repo=repo, checked_at=checked_at)


def describe(*, api_retry: float = 0, public_retry: float = 0) -> tuple[str, str, bool]:
    with _lock:
        status = _status
    now = time.time()
    fresh_quota = status.remaining is not None and status.reset_at > now
    if fresh_quota and status.remaining == 0:
        api_retry = max(api_retry, status.reset_at)
    limited = api_retry > now or public_retry > now
    if limited:
        label = "GitHub: limited"
    elif status.source == "Cached information":
        label = "GitHub: cached"
    elif status.source == "Public GitHub pages":
        label = "GitHub: public pages"
    elif fresh_quota:
        label = f"GitHub: {status.remaining}/{status.limit}"
    else:
        label = "GitHub status"
    details = []
    if fresh_quota:
        reset = datetime.fromtimestamp(status.reset_at).strftime("%H:%M:%S")
        details.append(
            f"API requests remaining: {status.remaining} of {status.limit} (last reported)."
        )
        details.append(f"Request window resets at {reset} (local time).")
    else:
        details.append("API requests remaining: unknown until the next GitHub response.")
    for host, retry in (("GitHub API", api_retry), ("Public GitHub pages", public_retry)):
        if retry > now:
            minutes = max(1, math.ceil((retry - now) / 60))
            at = datetime.fromtimestamp(retry).strftime("%H:%M:%S")
            details.append(f"{host} paused: retry in {minutes} min, at {at} (local time).")
    if status.source:
        checked = datetime.fromtimestamp(status.checked_at).strftime("%d %b %Y, %H:%M:%S")
        details.extend(
            [
                f"Most recent release result: {status.repo}",
                f"Source: {status.source}.",
                f"Last successful release check: {checked} (local time).",
            ]
        )
    else:
        details.append("No successful release check this session.")
    return label, "\n".join(details), limited
