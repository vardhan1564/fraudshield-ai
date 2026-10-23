"""Graph Explorer data (spec 04 endpoints 15-16, §2.5): a node's neighbourhood and mule rings."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.enums import EdgeType, RingStatus

NodeType = Literal["Account", "Payee", "Device", "IP"]


class GraphNode(BaseModel):
    id: str  # A:{user}, P:{vpa}, D:{fingerprint}, I:{ip} (spec 05 §3)
    type: NodeType
    label: str  # masked: phone, VPA, IP
    risk: float | None  # accounts: graph_score; payees: reputation; devices/IPs: null
    flags: list[str]  # detectors that fired, + mule_suspect for ring members


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    type: EdgeType
    amount: float | None
    ts: datetime


class RingRef(BaseModel):
    id: uuid.UUID
    members: list[str]
    score: float


class Neighbourhood(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdgeOut]
    rings: list[RingRef]
    truncated: bool  # node or edge cap reached; ask for a smaller depth


class RingItem(BaseModel):
    id: uuid.UUID
    members: list[str]
    size: int
    score: float
    detector: str
    status: RingStatus
    first_seen: datetime
    last_seen: datetime
