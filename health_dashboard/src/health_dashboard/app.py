import logging
from datetime import datetime
from pathlib import Path

import attrs
from health_data_service import (
    HealthDataClient,
    SleepSessionsRequest,
    TimeSeriesRequest,
    WorkoutsRequest,
)
from litestar import Litestar, Request, get
from litestar.response import Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).parent / "templates"
DASHBOARD_HTML = (_TEMPLATES / "dashboard.html").read_text()
HEART_RATE_HTML = (_TEMPLATES / "heart_rate.html").read_text()

_client: HealthDataClient | None = None


def _serialize(obj):
    """Recursively convert attrs instances to dicts for JSON response."""
    if attrs.has(type(obj)):
        d = {}
        for field in attrs.fields(type(obj)):
            val = getattr(obj, field.name)
            d[field.name] = _serialize(val)
        return d
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@get("/health")
async def health_check() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Dashboard page
# ---------------------------------------------------------------------------

@get("/")
async def index() -> Response:
    return Response(content=DASHBOARD_HTML, media_type="text/html")


@get("/heart-rate")
async def heart_rate_page() -> Response:
    return Response(content=HEART_RATE_HTML, media_type="text/html")


# ---------------------------------------------------------------------------
# API — fans out to all health-data providers and merges results
# ---------------------------------------------------------------------------

@get("/api/metrics")
async def proxy_metrics() -> dict:
    if _client is None:
        return {"metrics": []}
    try:
        metrics = await _client.list_metrics_merged()
        return {"metrics": [_serialize(m) for m in metrics]}
    except Exception:
        log.exception("Failed to fetch metrics")
        return {"metrics": []}


@get("/api/sleep-sessions")
async def proxy_sleep_sessions(request: Request) -> dict:
    if _client is None:
        return {"data": []}
    try:
        limit = int(request.query_params.get("limit", "100"))
        req = SleepSessionsRequest(limit=limit)
        sessions = await _client.get_sleep_sessions_merged(req)
        return {"data": [_serialize(s) for s in sessions]}
    except Exception:
        log.exception("Failed to fetch sleep sessions")
        return {"data": []}


@get("/api/time-series")
async def proxy_time_series(request: Request) -> dict:
    metric = request.query_params.get("metric", "")
    if _client is None:
        return {"metric_id": metric, "samples": []}
    try:
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        limit_str = request.query_params.get("limit")
        req = TimeSeriesRequest(
            metric=metric,
            start=datetime.fromisoformat(start) if start else None,
            end=datetime.fromisoformat(end) if end else None,
            limit=int(limit_str) if limit_str else None,
        )
        ts = await _client.get_time_series_merged(req)
        return _serialize(ts)
    except Exception:
        log.exception("Failed to fetch time series")
        return {"metric_id": metric, "samples": []}


@get("/api/workouts")
async def proxy_workouts(request: Request) -> dict:
    if _client is None:
        return {"data": []}
    try:
        req = WorkoutsRequest(
            workout_type=request.query_params.get("workout_type"),
            limit=int(request.query_params.get("limit", "100")),
        )
        workouts = await _client.get_workouts_merged(req)
        return {"data": [_serialize(w) for w in workouts]}
    except Exception:
        log.exception("Failed to fetch workouts")
        return {"data": []}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def on_startup() -> None:
    global _client
    try:
        _client = HealthDataClient()
        await _client.__aenter__()
    except Exception:
        log.warning("HealthDataClient not available (service env vars not set)")
        _client = None


async def on_shutdown() -> None:
    global _client
    if _client:
        await _client.__aexit__(None, None, None)
        _client = None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Litestar(
    route_handlers=[
        health_check,
        index,
        heart_rate_page,
        proxy_metrics,
        proxy_sleep_sessions,
        proxy_time_series,
        proxy_workouts,
    ],
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
)
