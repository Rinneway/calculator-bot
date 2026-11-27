from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

StatsSummary = tuple[int, int, int, int, float | None]
Event = tuple[str, int, str, int | None]


def read_events(path: Path) -> list[Event]:
    events = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(events, list) or any(
        not isinstance(row, list) or len(row) != 4
        or not isinstance(row[0], str) or type(row[1]) is not int
        or not isinstance(row[2], str) or (row[3] is not None and type(row[3]) is not int)
        for row in events
    ):
        raise ValueError("Invalid statistics file")
    return events


class EventStats:
    def __init__(self, events: list[Event], path: Path | None = None):
        self.events = events
        self.path = path

    async def log_event(self, user_id: int, event: str, value: int | None = None) -> None:
        entry = (datetime.now(timezone.utc).isoformat(timespec="seconds"), user_id, event, value)
        if self.path is not None:
            with NamedTemporaryFile(dir=self.path.parent, delete=False) as temporary:
                temp_path = Path(temporary.name)
            try:
                temp_path.write_text(json.dumps([*self.events, entry]), encoding="utf-8")
                os.replace(temp_path, self.path)
            finally:
                temp_path.unlink(missing_ok=True)
        self.events.append(entry)

    async def summary(self) -> StatsSummary:
        users = {"start": set(), "plan": set(), "want_card": set(), "survey": set()}
        total = count = 0
        for _, user_id, event, value in self.events:
            if event in users:
                users[event].add(user_id)
            if event == "plan" and value is not None and value > 0:
                total += value
                count += 1
        return (
            len(users["start"]),
            len(users["plan"]),
            len(users["want_card"]),
            len(users["survey"]),
            total / count if count else None,
        )
