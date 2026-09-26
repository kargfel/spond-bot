from datetime import datetime

from pydantic import BaseModel


class LatencyPoint(BaseModel):
    fired_at: datetime
    latency_ms: int
    user_name: str
    heading: str | None


class DailyRate(BaseModel):
    date: str          # "YYYY-MM-DD"
    success: int
    failed: int
    retry_success: int


class PerUserStats(BaseModel):
    user_name: str
    total: int
    success: int
    p50_ms: int | None
    p95_ms: int | None


class ChartsResponse(BaseModel):
    latency_scatter: list[LatencyPoint]
    daily_success_rate: list[DailyRate]
    per_user: list[PerUserStats]
