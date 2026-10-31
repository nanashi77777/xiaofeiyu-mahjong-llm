"""Per-round shared bank. Reconnects never refill it."""
from __future__ import annotations

import time


class TimeBank:
    def __init__(self, base=5.0, bank=20.0, margin=0.8):
        self.base, self.initial, self.margin = base, bank, margin
        self.round_uid = None
        self.remaining = bank
        self.spent_windows: set[str] = set()

    def enter_round(self, round_uid):
        if self.round_uid != round_uid:
            self.round_uid, self.remaining = round_uid, self.initial
            self.spent_windows.clear()

    def deadline(self, operation, *, now=None, verified=False):
        now = time.monotonic() if now is None else now
        if operation.historical:
            raise ValueError("historical operation has no fresh clock")
        # A fresh deal can include an observed animation allowance (e.g. 8s
        # instead of 5s). It is server time, separate from the 5s speed target.
        base = (operation.fixed_ms/1000 if verified else min(self.base,operation.fixed_ms/1000)) if operation.fixed_ms else self.base
        bank = self.remaining
        if verified and operation.add_ms is not None:
            self.remaining = bank = min(bank, operation.add_ms / 1000)
        # Until calibrated, never assume the server grants a fresh shared bank.
        if not verified:
            bank = 0
        deadline = operation.received_mono + base + bank - self.margin
        if deadline <= now:
            raise TimeoutError("operation expired")
        return deadline

    def charge(self, window_id, elapsed, *, fixed_ms=None):
        if window_id in self.spent_windows:
            return self.remaining
        self.spent_windows.add(window_id)
        base = fixed_ms/1000 if fixed_ms is not None else self.base
        self.remaining -= max(0, elapsed - base)
        return self.remaining
