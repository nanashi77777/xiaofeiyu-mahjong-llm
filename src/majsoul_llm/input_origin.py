"""Conservative attribution of observed client input; never chooses actions."""
from __future__ import annotations


def input_origin(payload):
    # Verified in the current client's actual timeout/automatic input traffic.
    # Other time values are not proof that a bot click produced the request.
    value = payload.get('timeuse')
    return 'client_automatic' if type(value) is int and value == 1000000 else 'unverified'
