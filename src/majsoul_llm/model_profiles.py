"""Offline named model configurations; no network probes or QQ write access."""
from __future__ import annotations

import os
import socket
import sqlite3
from typing import Literal

from pydantic import ConfigDict, Field, SecretStr, field_validator

from .config import MODEL_GATES, Settings, atomic_private_json, endpoint_url
from .domain import Record


class ProfileError(ValueError):
    pass


class ModelProfile(Record):
    model_config = ConfigDict(hide_input_in_errors=True)
    provider: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,64}$')
    base_url: str
    model: str = Field(min_length=1, max_length=200, pattern=r'^[^\s]+$')
    api_style: Literal['responses'] = 'responses'
    api_key: SecretStr = Field(exclude=True)
    validation_identity: str | None = None
    validated: dict[Literal['compact_validated', 'table_wire_validated', 'json_schema', 'stream_actions'], bool] = Field(default_factory=dict)

    _endpoint = field_validator('base_url')(endpoint_url)

    def settings(self, current: Settings, name: str):
        result = current.model_copy(update={'model_provider': self.provider, 'model_profile': name,
            'base_url': self.base_url, 'model': self.model, 'model_api_style': self.api_style,
            'api_key': self.api_key, 'stream_hedge_delay_seconds': 0})
        same = self.validation_identity == result.model_identity()
        for gate in MODEL_GATES:
            setattr(result, gate, bool(same and self.validated.get(gate, False)))
        result.model_validation_identity = result.model_identity()
        return result


class ProfileBook(Record):
    model_config = ConfigDict(hide_input_in_errors=True)
    schema_version: Literal['model-profiles.v1'] = 'model-profiles.v1'
    profiles: dict[str, ModelProfile] = Field(default_factory=dict)

    @field_validator('profiles')
    @classmethod
    def names(cls, profiles):
        import re
        if any(not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', name) for name in profiles):
            raise ValueError('invalid profile name')
        return profiles


class ModelProfiles:
    def __init__(self, settings: Settings):
        self.current = settings
        self.path = settings.runtime / 'model-profiles.json'

    def load(self):
        try:
            return ProfileBook.model_validate_json(self.path.read_text(encoding='utf-8')) if self.path.exists() else ProfileBook()
        except (ValueError, OSError):
            raise ProfileError('invalid local model profile file; details withheld') from None

    def list(self):
        book = self.load()
        return {'current': self.current.model_status(), 'profiles': [
            {**profile.settings(self.current, name).model_status(),
             'validation_matches': profile.validation_identity == profile.settings(self.current, name).model_identity()}
            for name, profile in book.profiles.items()]}

    def capture(self, name):
        book = self.load()
        if name in book.profiles:
            raise ProfileError('profile already exists; choose another name')
        profile = ModelProfile(provider=self.current.model_provider, base_url=self.current.base_url,
            model=self.current.model, api_style=self.current.model_api_style, api_key=self.current.api_key,
            validation_identity=self.current.model_identity(),
            validated={gate: getattr(self.current, gate) for gate in MODEL_GATES})
        # Validate the new name before writing any credentials.
        book = ProfileBook(profiles={**book.profiles, name: profile})
        data = book.model_dump(mode='json')
        for key, value in book.profiles.items():
            data['profiles'][key]['api_key'] = value.api_key.get_secret_value()
        atomic_private_json(self.path, data)
        return {'captured': name, **profile.settings(self.current, name).model_status()}

    def require_offline(self):
        with socket.socket() as probe:
            probe.settimeout(.3)
            if probe.connect_ex(('127.0.0.1', self.current.port)) == 0:
                raise ProfileError('stop the local service before selecting a model profile')
        archive = self.current.runtime / 'archive.sqlite'
        if archive.exists():
            connection = sqlite3.connect(archive.as_uri()+'?mode=ro', uri=True)
            try:
                if connection.execute("SELECT 1 FROM matches WHERE lifecycle='active' LIMIT 1").fetchone():
                    raise ProfileError('active archived match must be resolved before selecting a profile')
            finally:
                connection.close()

    def select(self, name):
        self.require_offline()
        profile = self.load().profiles.get(name)
        if profile is None:
            raise ProfileError('unknown model profile')
        override = os.getenv('MAJSOUL_API_KEY')
        if override and override != profile.api_key.get_secret_value():
            raise ProfileError('unset MAJSOUL_API_KEY override before selecting this profile')
        chosen = profile.settings(self.current, name)
        chosen.save_private()
        return {'selected': name, 'restart_required': True, **chosen.model_status(),
                'format_gates': {gate: getattr(chosen, gate) for gate in MODEL_GATES}}
