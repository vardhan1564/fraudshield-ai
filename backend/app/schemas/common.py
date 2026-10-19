"""Schemas shared by many endpoints."""

from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")

PageNo = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=200)]


class Page(BaseModel, Generic[T]):
    """Spec 04 pagination: `?page=1&page_size=50` -> items + page, page_size, total."""

    items: list[T]
    page: int
    page_size: int
    total: int
