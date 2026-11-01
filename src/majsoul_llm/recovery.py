"""Window-level failure accounting; recovery never chooses a Mahjong action."""
from __future__ import annotations


class Recovery:
    def __init__(self, limit):
        self.limit = limit
        self.failed = set()
        self.streak = 0
        self.total = 0
        self.recovered = 0
        self.phase = 'idle'

    def failure(self, match_id, window_id):
        key = (match_id, window_id)
        if key not in self.failed:
            self.failed.add(key)
            self.streak += 1
            self.total += 1
        return self.streak >= self.limit

    def confirmed(self):
        if self.streak:
            self.recovered += 1
        self.streak = 0
        self.phase = 'idle'

    def public(self):
        return {'phase': self.phase, 'consecutive_failed_windows': self.streak,
                'failed_windows': self.total, 'recoveries': self.recovered,
                'failure_window_limit': self.limit}
