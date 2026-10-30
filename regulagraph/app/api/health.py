from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["health"])


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/live", response_model=LiveResponse)
def live() -> LiveResponse:
    """Liveness only: the process is up. No dependency checks, no config leakage."""
    return LiveResponse()
