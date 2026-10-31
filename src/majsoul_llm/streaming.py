"""A complete first JSON field and separator commit one immutable model action."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


def unique_object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('duplicate model JSON key')
        result[key] = value
    return result


def strict_json(text):
    return json.loads(text,object_pairs_hook=unique_object)


class ActionPrefix:
    def __init__(self, context, format='nested'):
        self.context = context
        self.format = format
        self.committed = None

    def feed(self, text):
        if self.committed is not None:
            return None
        field = 'a' if self.format == 'short' else 'd'
        match = re.match(r'\s*\{\s*"'+field+r'"\s*:\s*',text)
        if not match:
            if len(text) > 128:
                raise ValueError('stream must begin with the decision object')
            return None
        start = match.end()
        try:
            value,end = json.JSONDecoder(object_pairs_hook=unique_object).raw_decode(text,start)
        except json.JSONDecodeError:
            if len(text)-start > 256:
                raise ValueError('action prefix exceeds its limit') from None
            return None
        rest = text[end:].lstrip()
        if not rest:
            return None
        if rest[0] != ',':
            raise ValueError('action object must end with a field separator')
        if self.format == 'short':
            value = {'q':self.context.q,'a':value}
        if (not isinstance(value,dict) or set(value) != {'q','a'}
                or value['q'] != self.context.q or not isinstance(value['a'],str)
                or value['a'] not in self.context.actions):
            raise ValueError('invalid committed request or action')
        self.committed = value
        return self.context.actions[value['a']]


@dataclass
class StreamChoice:
    action: Any
    metrics: dict
    completion: Any


STREAM_INSTRUCTION = '''
本次使用流式动作提交。输出严格JSON，仅d、r、p，必须按此顺序：
{"d":{"q":"原样请求标识","a":"合法action_id"},"r":"8至20字简短解释","p":null或完整计划}。
d必须最先输出，其右花括号和紧接的逗号是不可撤销的动作提交点，随后程序立即执行。
提交前完成动作选择；后续r/p只解释该动作，不能更改或撤回d，不得再次输出q/a/d。
尽快先完成d；r为可展示的简短理由，不输出长思考过程。p沿用原计划契约。
'''


def stream_prompt(original,format):
    # Keep rules/plan semantics while replacing the obsolete output contract.
    lines = [line for line in original.splitlines() if not line.startswith('输出严格JSON对象，仅q、a、r、p')]
    if format == 'short':
        return '\n'.join(lines)+'''
流式输出严格JSON，仅a、r、p，必须按此顺序：{"a":"合法action_id","r":"8至20字简短解释","p":null或完整计划}。
完整a字符串及其后逗号是不可撤销动作提交点，程序立即执行；不要回显q，连接已绑定本请求。
提交前完成动作选择，后续r/p只能解释此动作，不能改写或再次输出a。尽快完成首字段；p沿用计划契约。
'''
    return '\n'.join(lines)+STREAM_INSTRUCTION
