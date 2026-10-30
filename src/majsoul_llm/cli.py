"""Local entry points. Never emit credentials in status/probe reports."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import Settings
from .model import ModelClient
from .store import Store


async def probe(settings):
    store = Store(settings.runtime / "archive.sqlite")
    client = ModelClient(settings, store)
    try:
        result, metrics = await client.request({"q":"probe","actions":[{"action_id":"a0","kind":"pass"}]},purpose="probe",
                                              system='Return exactly JSON: {"q":"probe","action_id":"a0","reason":"渠道测试","plan_update":null}')
        return {"valid":result.get("action_id")=="a0", "metrics":metrics}
    finally:
        await client.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("serve","status","probe","init-config","panel","import-proto"))
    parser.add_argument("--path",type=Path)
    args = parser.parse_args()
    settings = Settings.load()
    if args.command == "init-config":
        if not (settings.runtime / "private.json").exists():
            settings.save_private()
        print("Local private configuration ready; environment MAJSOUL_API_KEY overrides the stored key.")
    elif args.command == "serve":
        import uvicorn
        from .app import create_app
        if not (settings.runtime / "private.json").exists():
            settings.save_private()
        uvicorn.run(create_app(settings),host="127.0.0.1",port=settings.port,access_log=False)
    elif args.command == "panel":
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{settings.port}/#"+settings.control_token.get_secret_value())
        print("Opened the local panel; control credential stays in the local URL fragment.")
    elif args.command == "probe":
        print(json.dumps(asyncio.run(probe(settings)),ensure_ascii=False))
    elif args.command == "import-proto":
        from .protocol import Protocol
        if not args.path:
            parser.error("--path required")
        destination = settings.runtime / "protocol/protocol.proto"
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(args.path.read_bytes())
        protocol = Protocol.from_proto(destination,settings.runtime / "generated")
        print(json.dumps({"protocol_sha256":protocol.fingerprint}))
    else:
        store=Store(settings.runtime / "archive.sqlite")
        from .queries import Queries
        print(json.dumps(Queries(store,settings.read_token.get_secret_value()).live(),ensure_ascii=False))
        store.close()


if __name__ == "__main__":
    main()
