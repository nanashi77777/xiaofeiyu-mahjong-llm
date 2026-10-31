"""Read-only Liqi decoder. Schema comes from the client's public resources."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

from google.protobuf import descriptor_pool, json_format, message_factory

from .lobby_session import LOBBY_METHODS, sanitize_lobby


def xor_action(data: bytes) -> bytes:
    keys = (0x84, 0x5E, 0x4E, 0x42, 0x39, 0xA2, 0x1F, 0x60, 0x1C)
    base = 23 ^ len(data)
    return bytes(b ^ ((base + 5 * i + keys[i % 9]) & 255) for i, b in enumerate(data))


class Protocol:
    def __init__(self, descriptor_bytes: bytes):
        self.pool = descriptor_pool.DescriptorPool()
        self.file = self.pool.AddSerializedFile(descriptor_bytes)
        self.fingerprint = hashlib.sha256(descriptor_bytes).hexdigest()
        self.pending: dict[tuple[str, int], tuple[str, str, dict | None]] = {}

    @classmethod
    def from_proto(cls, path: Path, output: Path):
        output.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "grpc_tools.protoc", "-I" + str(path.parent),
                        "--python_out=" + str(output), str(path)], check=True, capture_output=True)
        generated = output / (path.stem + "_pb2.py")
        spec = importlib.util.spec_from_file_location("runtime_liqi", generated)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cls(module.DESCRIPTOR.serialized_pb)

    def message(self, name: str):
        return message_factory.GetMessageClass(self.pool.FindMessageTypeByName(name.lstrip(".")))

    def decode(self, name: str, raw: bytes) -> dict[str, Any]:
        msg = self.message(name).FromString(raw)
        return json_format.MessageToDict(msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)

    def action(self, name: str, raw: bytes, *, historical: bool = False):
        if not name.startswith("Action"):
            raise ValueError("unsupported action")
        return self.decode("lq." + name, raw if historical else xor_action(raw))

    def frame(self, connection: str, raw: bytes, from_client: bool):
        if not raw or raw[0] not in (1, 2, 3):
            return None
        kind = raw[0]
        if kind == 1:
            wrapper = self.decode("lq.Wrapper", raw[1:])
            name = wrapper.get("name", "")
        else:
            if len(raw) < 3:
                raise ValueError("truncated RPC frame")
            request_id = struct.unpack_from("<H", raw, 1)[0]
            wrapper = self.decode("lq.Wrapper", raw[3:])
            name = wrapper.get("name", "")
            if kind == 2:
                if name.startswith(".lq.Lobby."):
                    service, method = name.lstrip(".").rsplit(".", 1)
                    if method not in LOBBY_METHODS:
                        return None
                    descriptor = self.pool.FindServiceByName(service).methods_by_name[method]
                    # Login request bytes can include passwords/tokens. Don't
                    # decode or forward them, even to an in-memory observer.
                    self.pending[(connection, request_id)] = (name, descriptor.output_type.full_name, None)
                    return None
                # Only gameplay RPCs. Auth values are never returned or persisted.
                if not name.startswith(".lq.FastTest."):
                    return None
                service, method = name.lstrip(".").rsplit(".", 1)
                descriptor = self.pool.FindServiceByName(service).methods_by_name[method]
                request = self.decode(descriptor.input_type.full_name, base64.b64decode(wrapper.get("data", "")))
                if method == "authGame":
                    request = {k: request[k] for k in ("account_id", "game_uuid") if k in request}
                elif method not in ("syncGame", "inputOperation", "inputChiPengGang", "confirmNewRound", "enterGame"):
                    return None
                self.pending[(connection, request_id)] = (name, descriptor.output_type.full_name,request if method in ("inputOperation","inputChiPengGang") else None)
                return {"method": name, "direction": "request", "data": request, "connection": connection,"request_id":request_id}
            route = self.pending.pop((connection, request_id), None)
            if route is None:
                return None
            name, response_type, original_input = route
        data = base64.b64decode(wrapper.get("data", ""))
        if kind == 1:
            if name not in (".lq.ActionPrototype", ".lq.NotifyGameEndResult", ".lq.NotifyGameTerminate"):
                return None
            decoded = self.decode(name, data)
            if name == ".lq.ActionPrototype":
                decoded["data"] = self.action(decoded["name"], base64.b64decode(decoded.get("data", "")))
        else:
            decoded = self.decode(response_type, data)
            if name.startswith(".lq.Lobby."):
                decoded = sanitize_lobby(name.rsplit(".", 1)[-1], decoded)
            if name.endswith("authGame"):
                decoded = {k: decoded[k] for k in ("error", "seat_list", "game_config", "is_game_start") if k in decoded}
            restore = decoded.get("game_restore")
            if restore:
                for action in restore.get("actions", []):
                    action["data"] = self.action(action["name"], base64.b64decode(action.get("data", "")), historical=True)
        result={"method": name, "direction": "notify" if kind == 1 else "response", "data": decoded, "connection": connection}
        if kind==3:
            result.update(request_id=request_id,original_input=original_input)
        return result
