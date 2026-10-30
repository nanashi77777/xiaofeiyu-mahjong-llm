"""Read-only versioned yaku reference for review, not realtime prompt stuffing."""
import json
from pathlib import Path

from .config import ROOT


class YakuReference:
    def __init__(self, path: Path | None = None):
        self.catalog = json.loads((path or ROOT / "reference/yaku/standard-four-player.v1.json").read_text(encoding="utf-8"))
        self.entries = {e["id"]: e for e in self.catalog["entries"]}
        if len(self.entries) != len(self.catalog["entries"]):
            raise ValueError("duplicate yaku ID")
        if not self.catalog.get('catalog_version') or len(self.entries)!=50:
            raise ValueError('unsupported yaku catalog')

    def read(self, ids: list[str] | None = None, topic: str = "", all_entries=False):
        if all_entries:
            selected = set(self.entries)
        else:
            selected = set(ids or [])
            for group in self.catalog.get("families", []):
                if group["id"] in selected:
                    selected.remove(group["id"])
                    selected.update(group["members"])
            if topic:
                selected.update(key for key, e in self.entries.items() if topic.casefold() in json.dumps(e, ensure_ascii=False).casefold())
        unknown = selected - self.entries.keys()
        if unknown:
            raise ValueError("unknown yaku reference")
        related = set(selected)
        for key in selected:
            related.update(self.entries[key].get("replaces", []))
            related.update(self.entries[key].get("incompatible_with", []))
        return {"catalog_version": self.catalog["catalog_version"],
                "entries": [e for key, e in self.entries.items() if key in related], "requested": sorted(selected)}
