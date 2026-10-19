"""Finding rows by the ids clients use (their own ids or our UUIDs)."""

import uuid

from sqlalchemy import ColumnElement, or_

from app.models import Transaction, User


def by_ref(txn_ref: str) -> ColumnElement[bool]:
    """A transaction by the client's txn_id or by our UUID."""
    try:
        return or_(Transaction.txn_id == txn_ref, Transaction.id == uuid.UUID(txn_ref))
    except ValueError:
        return Transaction.txn_id == txn_ref


def user_filter(user_ref: str) -> ColumnElement[bool]:
    """A customer by external id (U1029) or UUID."""
    try:
        return or_(User.external_id == user_ref, User.id == uuid.UUID(user_ref))
    except ValueError:
        return User.external_id == user_ref
