"""
Common Pydantic schemas — pagination, filters, standard responses
"""

from typing import Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def create(cls, items: List[T], total: int, page: int, page_size: int):
        import math
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=math.ceil(total / page_size) if page_size else 1,
        )


class HealthResponse(BaseModel):
    status: str
    version: str
    database: bool
    collectors: dict


class StatusResponse(BaseModel):
    success: bool
    message: str


class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
