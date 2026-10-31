"""Private local settings. Keys never appear in model dumps or exception logs."""
from __future__ import annotations

import json
import os
import secrets
import hashlib
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, SecretStr, field_validator

from .domain import Record

ROOT = Path(__file__).resolve().parents[2]

MODEL_GATES = ('compact_validated', 'table_wire_validated', 'json_schema', 'stream_actions')


def endpoint_url(value: str) -> str:
    try:
        url = urlsplit(value)
        _ = url.port
    except ValueError:
        raise ValueError('invalid model endpoint syntax') from None
    if (not url.hostname or url.username is not None or url.password is not None
            or url.query or url.fragment or '?' in value or '#' in value
            or any(c.isspace() for c in value)
            or (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1')))):
        raise ValueError('model endpoint must be HTTPS without credentials, query or fragment')
    return value.rstrip('/')


def atomic_private_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=path.parent, prefix='.private-', suffix='.tmp', delete=False) as target:
            name = target.name
            json.dump(data, target, ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


class Settings(Record):
    model_config = ConfigDict(hide_input_in_errors=True)
    base_url: str = "https://model-provider.invalid/v1"
    model: str = "your-model-id"
    model_provider: str = Field(default='default', pattern=r'^[a-zA-Z0-9_.-]{1,64}$')
    model_profile: str = Field(default='default', pattern=r'^[a-zA-Z0-9_.-]{1,64}$')
    model_api_style: Literal['responses'] = 'responses'
    model_validation_identity: str | None = None
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
    stream_hedge_delay_seconds: float = Field(default=0,ge=0,le=5)
    integration_test: bool = False
    input_mode: Literal['physical_foreground','browser_background'] = 'physical_foreground'

    _endpoint = field_validator('base_url')(endpoint_url)

    def model_identity(self):
        identity = [self.model_provider, self.base_url, self.model, self.model_api_style,
                    self.api_key.get_secret_value()]
        return hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()

    def model_status(self):
        return {'provider': self.model_provider, 'profile': self.model_profile,
                'model': self.model, 'api_style': self.model_api_style, 'reasoning': 'none',
                'identity': self.model_identity()[:16]}

    @classmethod
    def load(cls, path: Path | None = None):
        path = path or ROOT / "runtime/private.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if os.getenv("MAJSOUL_API_KEY"):
            data["api_key"] = os.environ["MAJSOUL_API_KEY"]
        settings = cls(**data)
        if settings.model_validation_identity and settings.model_validation_identity != settings.model_identity():
            for gate in MODEL_GATES:
                setattr(settings, gate, False)
            settings.stream_hedge_delay_seconds = 0
        return settings

    def save_private(self, path: Path | None = None):
        path = path or self.runtime / "private.json"
        data = self.model_dump(mode="json")
        for key in ("api_key", "control_token", "read_token", "navigation_token"):
            data[key] = getattr(self, key).get_secret_value()
        atomic_private_json(path, data)
        return path
