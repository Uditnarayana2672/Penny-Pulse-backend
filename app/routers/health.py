from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthOut(BaseModel):
    status: str


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Liveness only. It does not touch the database, so it stays honest about which
    of the two is down."""
    return HealthOut(status="ok")
