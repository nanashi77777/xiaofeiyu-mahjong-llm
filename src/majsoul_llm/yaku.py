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
        if (self.catalog.get('schema_version') != 'yaku-reference.v1'
                or not self.catalog.get('catalog_version') or len(self.entries)!=50):
            raise ValueError('unsupported yaku catalog')
        source_ids = {s['id'] for s in self.catalog['sources']}
        for entry in self.entries.values():
            if (entry.get('category') not in ('normal','yakuman','bonus','score_limit','special_settlement')
                    or entry.get('verification_status') not in ('reference_cross_checked','partially_verified')
                    or not entry.get('conditions') or not entry.get('source_refs')
                    or not set(entry['source_refs']) <= source_ids):
                raise ValueError('invalid yaku classification or provenance')
            for key in ('closed_han','open_han','yakuman_multiplier'):
                value = entry.get(key)
                if value is not None and (type(value) is not int or value < 1):
                    raise ValueError('invalid yaku value')
            if entry['category'] == 'normal' and not (entry.get('closed_han') or entry.get('open_han')):
                raise ValueError('normal yaku lacks han')
            if entry['category'] == 'yakuman' and not entry.get('yakuman_multiplier'):
                raise ValueError('yakuman lacks multiplier')
            if (entry.get('requires_closed') and entry.get('open_han') is not None
                    or not set(entry.get('replaces',[])+entry.get('incompatible_with',[])) <= self.entries.keys()):
                raise ValueError('invalid yaku relation')
            if not isinstance(entry.get('rule_switches'),list) or any(not isinstance(s,str) for s in entry['rule_switches']):
                raise ValueError('invalid yaku rule switches')
        if any(not set(f['members']) <= self.entries.keys() for f in self.catalog.get('families',[])):
            raise ValueError('unknown yaku family member')

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
