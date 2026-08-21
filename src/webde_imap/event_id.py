import hashlib


def compute_event_id(account_scope, folder, uidvalidity, uid, message_id):
    """Stable, deterministic ID for downstream (Gmail-side) dedup.

    Includes uidvalidity because a bare UID is only unique within one
    UIDVALIDITY epoch; message_id (when present) adds extra collision
    resistance without being required.
    """
    raw = f"{account_scope}|{folder}|{uidvalidity}|{uid}|{message_id or ''}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"webde-{digest}"
