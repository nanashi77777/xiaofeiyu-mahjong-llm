"""Private local settings. Keys never appear in model dumps or exception logs."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr

from .domain import Record

ROOT = Path(__file__).resolve().parents[2]


class Settings(Record):
    base_url: str = "https://model-provider.invalid/v1"
    model: str = "your-model-id"
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), exclude=True)
    control_token: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(32)), exclude=True)
    read_token: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(32)), exclude=True)
    navigation_token: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(32)), exclude=True)
    port: int = 8765
    debug_port: int = 9223
    game_url: str = "https://game.maj-soul.com/1/"
    chrome_path: str = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    runtime: Path = ROOT / "runtime"
    base_seconds: float = 5
    bank_seconds: float = 20
    execution_margin: float = 0.8
    timer_verified: bool = False
    recovery_timer_verified: bool = False
    compact_validated: bool = False
    table_wire_validated: bool = False
    request_timeout: float = 25
    json_schema: bool = False
    stream_actions: bool = False
    stream_action_format: Literal['nested','short'] = 'nested'
    integration_test: bool = False
    input_mode: Literal['physical_foreground','browser_background'] = 'physical_foreground'

    @classmethod
    def load(cls, path: Path | None = None):
        path = path or ROOT / "runtime/private.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if os.getenv("MAJSOUL_API_KEY"):
            data["api_key"] = os.environ["MAJSOUL_API_KEY"]
        return cls(**data)

    def save_private(self, path: Path | None = None):
        path = path or self.runtime / "private.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json")
        for key in ("api_key", "control_token", "read_token", "navigation_token"):
            data[key] = getattr(self, key).get_secret_value()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        return path
