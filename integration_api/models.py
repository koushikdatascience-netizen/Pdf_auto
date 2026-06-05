"""FastAPI request models."""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field


class PurchasePreviewRequest(BaseModel):
    companycode: str = Field(min_length=1)
    yearcode: str = Field(min_length=1)
    invoice: Dict[str, Any]
    strict_total: bool = True


class PurchaseInsertRequest(BaseModel):
    approval_token: str = Field(min_length=20)

