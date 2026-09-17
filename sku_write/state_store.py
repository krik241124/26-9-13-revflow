from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json


class StateStore:
    def __init__(self, path: Path | None):
        self.path = path
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def latest_for_sku(self, sku: str) -> dict | None:
        if self.path is None or not self.path.exists():
            return None
        latest = None
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if item.get("sku") == sku:
                    latest = item
        return latest

    def append(self, item: dict) -> None:
        if self.path is None:
            return
        row = dict(item)
        row["time"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
