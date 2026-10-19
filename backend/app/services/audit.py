"""Append-only audit trail (PRD §8.6, spec 08: every case update, rule change, model deploy,
login). `record()` adds the row to the caller's session, so it commits or rolls back together
with the change it describes. Never put passwords, tokens or full phone numbers in it."""

import ipaddress
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def valid_ip(value: str | None) -> str | None:
    """INET column: keep real addresses only ('unknown', 'testclient' -> None)."""
    try:
        return None if value is None else str(ipaddress.ip_address(value))
    except ValueError:
        return None


def record(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    entity: str,
    entity_id: str | uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    """`action` is `<entity>.<verb>`, e.g. `case.update`, `auth.login`."""
    row = AuditLog(
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=None if entity_id is None else str(entity_id),
        before_json=before,
        after_json=after,
        ip=valid_ip(ip),
    )
    session.add(row)
    return row
