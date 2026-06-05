import json
import math
import random
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest
from openhost_test_harness import OpenhostStack


def _generate_hr_samples():
    """Generate 24h of realistic heart rate data with gaps."""
    random.seed(42)
    now = int(time.time())
    samples = []
    t = now - 24 * 3600

    while t < now:
        hour_utc = (t % 86400) / 3600

        # Gap from 10–12 UTC to test gap rendering
        if 10 <= hour_utc < 12:
            t += 300
            continue

        if hour_utc < 7 or hour_utc >= 23:
            hr = 58 + 5 * math.sin(t / 1800) + random.gauss(0, 2)
        else:
            hr = 75 + 10 * math.sin(t / 600) + random.gauss(0, 6)

        hr = max(40, min(160, round(hr, 1)))
        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))
        samples.append({"timestamp": ts_iso, "value": hr})
        t += 300

    return samples


_HR_SAMPLES = _generate_hr_samples()

_WORKOUT_ID = "wk-mock-1"
_WORKOUTS_PATH = "/api/services/v2/call/health/v1/workouts"


def _sv(metric_id, display, unit, value):
    return {"metric_id": metric_id, "display_name": display, "unit": unit,
            "value": value, "source": "mock"}


def _workout_summary():
    """Scalar-only summary, as the list endpoint returns it."""
    return {
        "id": _WORKOUT_ID,
        "start": "2026-06-01T08:00:00+00:00",
        "end": "2026-06-01T08:30:00+00:00",
        "workout_type": "running",
        "source": "mock",
        "duration": _sv("duration", "Duration", "min", 30.0),
        "calories": _sv("calories", "Calories", "kcal", 300.0),
        "distance": _sv("distance", "Distance", "m", 5000.0),
        "average_heart_rate": _sv("average_heart_rate", "Avg Heart Rate", "bpm", 150.0),
    }


def _workout_detail(workout_id):
    """Full detail: summary plus the heart-rate trace and route."""
    w = _workout_summary()
    w["id"] = workout_id
    w["heart_rate"] = {
        "metric_id": "heart_rate", "display_name": "Heart Rate", "unit": "bpm",
        "source": "mock",
        "samples": [
            {"timestamp": "2026-06-01T08:00:00+00:00", "value": 140.0},
            {"timestamp": "2026-06-01T08:01:00+00:00", "value": 150.0},
        ],
    }
    w["route_gpx"] = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        '<trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-06-01T08:00:00Z</time></trkpt>'
        '<trkpt lat="37.001" lon="-122.001"><ele>11</ele><time>2026-06-01T08:01:00Z</time></trkpt>'
        '</trkseg></trk></gpx>'
    )
    return w


class _MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/services/v2/providers":
            return self._json({"providers": [{
                "app_id": "mock-provider",
                "app_name": "mock-health",
                "service_version": "0.1.0",
                "endpoint": "/api/",
                "status": "running",
                "is_default": True,
            }]})

        if path.startswith("/api/services/v2/call/health/v1/time-series"):
            metric = (qs.get("metric") or [""])[0]
            if metric == "heart_rate":
                start = (qs.get("start") or [""])[0]
                filtered = _HR_SAMPLES
                if start:
                    filtered = [s for s in filtered if s["timestamp"] >= start]
                return self._json({
                    "metric_id": "heart_rate", "display_name": "Heart Rate",
                    "unit": "bpm", "source": "mock", "samples": filtered,
                })
            return self._json({
                "metric_id": metric, "display_name": metric,
                "unit": None, "source": "mock", "samples": [],
            })

        if path.startswith("/api/services/v2/call/health/v1/sleep-sessions"):
            return self._json({"data": []})

        if path.startswith(_WORKOUTS_PATH):
            workout_id = path[len(_WORKOUTS_PATH):].strip("/")
            if workout_id:
                if workout_id == _WORKOUT_ID:
                    return self._json(_workout_detail(workout_id))
                return self._json({"error": "not found"}, 404)
            return self._json({"data": [_workout_summary()]})

        if path.startswith("/api/services/v2/call/health/v1/metrics"):
            return self._json({"metrics": [
                {"metric_id": "heart_rate", "display_name": "Heart Rate", "unit": "bpm"},
            ]})

        self._json({"error": "not found"}, 404)

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def mock_service_port():
    server = HTTPServer(("0.0.0.0", 0), _MockHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


@pytest.fixture(scope="session")
def stack(mock_service_port):
    with OpenhostStack(
        app_dir=Path(__file__).resolve().parent.parent,
        extra_env={
            "OPENHOST_ROUTER_URL": f"http://host.containers.internal:{mock_service_port}",
            "OPENHOST_APP_TOKEN": "test-token",
        },
    ) as s:
        yield s
