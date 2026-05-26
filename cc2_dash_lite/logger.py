from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Deque

_LOGS: Deque[dict] = deque(maxlen=300)


def log(level: str, message: str, source: str = "app", **extra) -> None:
    _LOGS.appendleft(
        {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": level.upper(),
            "source": source,
            "message": message,
            "extra": extra,
        }
    )


def get_logs(limit: int = 120) -> list[dict]:
    return list(_LOGS)[:limit]
