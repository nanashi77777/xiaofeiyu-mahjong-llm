"""Mahjong usage groups; old untagged requests never count toward a new key."""
from __future__ import annotations


def usage_summary(store, current_credential_id):
    rows = store.db.execute("""
        SELECT json_extract(payload,'$.billing_scope') AS scope,
               json_extract(payload,'$.credential_id') AS credential_id,
               json_extract(payload,'$.purpose') AS request_purpose,
               COUNT(*) AS requests,
               COUNT(json_extract(payload,'$.usage.total_tokens')) AS with_usage,
               SUM(json_extract(payload,'$.usage.input_tokens')) AS input_tokens,
               SUM(json_extract(payload,'$.usage.output_tokens')) AS output_tokens,
               SUM(json_extract(payload,'$.usage.total_tokens')) AS total_tokens,
               SUM(json_extract(payload,'$.usage.input_tokens_details.cached_tokens')) AS cached_tokens,
               SUM(json_extract(payload,'$.usage.output_tokens_details.reasoning_tokens')) AS reasoning_tokens
        FROM records WHERE purpose='token_usage' GROUP BY scope,credential_id,request_purpose
    """)
    groups = {}
    fields = ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens")
    for row in rows:
        scope = "mahjong" if row["scope"] == "mahjong" and row["credential_id"] else "legacy_unattributed"
        credential_id = row["credential_id"] if scope == "mahjong" else None
        key = (scope, credential_id)
        group = groups.setdefault(key, {"scope":scope, "credential_id":credential_id,
            "current":scope == "mahjong" and credential_id == current_credential_id,
            "requests":0, "with_usage":0, "without_usage":0, "purposes":{}, **dict.fromkeys(fields)})
        group["requests"] += row["requests"]
        group["with_usage"] += row["with_usage"]
        group["without_usage"] += row["requests"] - row["with_usage"]
        purpose = row["request_purpose"] or "unknown"
        by_purpose = group["purposes"].setdefault(purpose, {"requests":0, "with_usage":0, **dict.fromkeys(fields)})
        by_purpose["requests"] += row["requests"]
        by_purpose["with_usage"] += row["with_usage"]
        for field in fields:
            if row[field] is not None:
                group[field] = (group[field] or 0) + row[field]
                by_purpose[field] = (by_purpose[field] or 0) + row[field]
    return {"billing_scope":"mahjong", "current_credential_id":current_credential_id,
            "totals_are_known_usage_only":True, "groups":list(groups.values())}
