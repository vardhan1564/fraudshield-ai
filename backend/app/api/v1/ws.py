"""WebSockets (spec 04 §3): `/ws/alerts` for analysts, `/ws/customer` for customers.

Each connection subscribes to its Redis pub/sub channel (spec 02 §6: `ws:analysts`,
`ws:user:{user_id}`) and forwards every message unchanged, so it works with any number of API
workers. Auth is the access token in `?token=` (browsers can't set headers on a WebSocket); a bad
token or wrong role closes with 1008 before the handshake completes. The connection closes when
the token expires; the client reconnects with a fresh one.
"""

import asyncio
import contextlib
from datetime import UTC, datetime

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from redis.exceptions import RedisError
from starlette.websockets import WebSocketState

from app.core.errors import AppError
from app.core.security import decode_token
from app.models import User
from app.models.enums import Role
from app.services import notify

router = APIRouter(prefix="/ws", tags=["websockets"])

POLICY_VIOLATION = status.WS_1008_POLICY_VIOLATION


async def authenticate(ws: WebSocket, token: str, role: Role) -> tuple[User, float] | None:
    """(user, seconds until the token expires), or None."""
    try:
        claims = decode_token(token, "access", ws.app.state.settings)
    except AppError:
        return None
    async with ws.app.state.sessionmaker() as session:
        user = await session.get(User, claims.user_id)
    if user is None or user.role != role:
        return None
    return user, (claims.expires_at - datetime.now(UTC)).total_seconds()


async def relay(ws: WebSocket, channel: str, ttl_s: float) -> None:
    pubsub = ws.app.state.redis.pubsub()
    await pubsub.subscribe(channel)  # before accept: nothing published after connect is lost
    await ws.accept()

    async def forward() -> None:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is not None:
                await ws.send_text(msg["data"])

    async def until_client_leaves() -> None:
        with contextlib.suppress(WebSocketDisconnect):
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass  # clients don't send anything; ignore it

    expiry = asyncio.create_task(asyncio.sleep(max(ttl_s, 0)))
    tasks = [asyncio.create_task(forward()), asyncio.create_task(until_client_leaves()), expiry]
    expired = False
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        expired = expiry.done() and not expiry.cancelled()  # before we cancel the rest
    finally:
        # Shielded: a cancellation (server shutdown, the client's close racing us) must not
        # stop the clean-up half way and leak the pub/sub connection.
        with anyio.CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with contextlib.suppress(RedisError):
                await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            if ws.client_state == WebSocketState.CONNECTED:
                with contextlib.suppress(RuntimeError):  # client already gone
                    await ws.close(code=POLICY_VIOLATION if expired else 1000)


@router.websocket("/alerts")
async def alerts(ws: WebSocket, token: str = "") -> None:
    """Analysts: `alert.created`, `explanation.ready`, `case.updated`."""
    found = await authenticate(ws, token, Role.ANALYST)
    if found is None:
        await ws.close(code=POLICY_VIOLATION)
        return
    await relay(ws, notify.ANALYSTS, found[1])


@router.websocket("/customer")
async def customer(ws: WebSocket, token: str = "") -> None:
    """The logged-in customer only: `explanation.ready`, `approval.requested`,
    `approval.resolved`."""
    found = await authenticate(ws, token, Role.CUSTOMER)
    if found is None:
        await ws.close(code=POLICY_VIOLATION)
        return
    user, ttl_s = found
    await relay(ws, notify.user_channel(user.id), ttl_s)
