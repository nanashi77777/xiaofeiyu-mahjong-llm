"""Provision fixed local QQ operation capability without printing credentials."""
import argparse
from pathlib import Path

from majsoul_llm.config import Settings, atomic_private_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qq-root',type=Path,required=True)
    args=parser.parse_args()
    root=args.qq_root.resolve()
    if not (root/'bridge/src/mcp-qq-chat.js').is_file():
        parser.error('expected the configured QQ deployment')
    settings=Settings.load()
    settings.save_private()
    atomic_private_json(root/'runtime/mahjong-operations.json',{
        'baseUrl':f'http://127.0.0.1:{settings.port}',
        'operationToken':settings.operation_token.get_secret_value(),
        'serviceRoot':str(Path(__file__).resolve().parents[1])})
    print('Private operation capability provisioned; restart the Mahjong service before use. No credential output.')


if __name__=='__main__':
    main()
