import asyncio
import logging
import os

import httpx
from litestar import Litestar, Request, get
from litestar.response import Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

SERVICE_URL = "github.com/imbue-openhost/health-data-service-spec"

_http_client: httpx.AsyncClient | None = None


def _router_url() -> str:
    return os.environ.get("OPENHOST_ROUTER_URL", "").rstrip("/")


def _service_base_url() -> str:
    return f"{_router_url()}/api/services/v2/call/health"


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("OPENHOST_APP_TOKEN", "")
    return {"Authorization": f"Bearer {token}"}


async def _discover_providers() -> list[dict]:
    assert _http_client is not None
    try:
        resp = await _http_client.get(
            f"{_router_url()}/api/services/v2/providers",
            params={"service": SERVICE_URL},
            headers=_auth_headers(),
        )
        if resp.status_code == 200:
            return resp.json().get("providers", [])
    except Exception:
        log.exception("Provider discovery failed")
    return []


async def _fan_out_get(path: str, params: dict | None = None) -> list[dict]:
    """Call all running providers in parallel, return list of parsed JSON responses."""
    assert _http_client is not None
    providers = await _discover_providers()
    running = [p for p in providers if p.get("status") == "running"]

    if not running:
        url = f"{_service_base_url()}{path}"
        resp = await _http_client.get(url, params=params, headers=_auth_headers())
        return [resp.json()] if resp.status_code == 200 else []

    async def _call(provider: dict) -> dict | None:
        headers = {**_auth_headers(), "X-OpenHost-Provider": provider["app_id"]}
        try:
            resp = await _http_client.get(
                f"{_service_base_url()}{path}", params=params, headers=headers,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            log.warning("Service call to %s failed", provider.get("app_name"))
        return None

    results = await asyncio.gather(*[_call(p) for p in running])
    return [r for r in results if r is not None]


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
    results = await _fan_out_get("/v1/metrics")
    seen: set[str] = set()
    merged: list[dict] = []
    for r in results:
        for m in r.get("metrics", []):
            mid = m.get("metric_id")
            if mid and mid not in seen:
                seen.add(mid)
                merged.append(m)
    return {"metrics": merged}


@get("/api/sleep-sessions")
async def proxy_sleep_sessions(request: Request) -> dict:
    results = await _fan_out_get("/v1/sleep-sessions", dict(request.query_params))
    all_sessions: list[dict] = []
    for r in results:
        all_sessions.extend(r.get("data", []))
    all_sessions.sort(key=lambda s: s.get("start", ""), reverse=True)
    limit = int(request.query_params.get("limit", "100"))
    return {"data": all_sessions[:limit]}


@get("/api/time-series")
async def proxy_time_series(request: Request) -> dict:
    results = await _fan_out_get("/v1/time-series", dict(request.query_params))
    if not results:
        return {"metric_id": request.query_params.get("metric", ""), "samples": []}
    merged = dict(results[0])
    all_samples: list[dict] = []
    for r in results:
        all_samples.extend(r.get("samples", []))
    all_samples.sort(key=lambda s: s.get("timestamp", ""))
    merged["samples"] = all_samples
    return merged


@get("/api/workouts")
async def proxy_workouts(request: Request) -> dict:
    results = await _fan_out_get("/v1/workouts", dict(request.query_params))
    all_workouts: list[dict] = []
    for r in results:
        all_workouts.extend(r.get("data", []))
    all_workouts.sort(key=lambda s: s.get("start", ""), reverse=True)
    return {"data": all_workouts}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def on_startup() -> None:
    global _http_client
    _http_client = httpx.AsyncClient(timeout=30)


async def on_shutdown() -> None:
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Health Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css">
<script src="https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 1.5rem; max-width: 1100px; margin: 0 auto;
  }
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; }
  h1 { font-size: 1.5rem; }
  section { margin-bottom: 2rem; }
  .section-title {
    font-size: 1.15rem; font-weight: 600; margin-bottom: 0.75rem;
    display: flex; align-items: baseline; gap: 0.6rem;
  }
  .section-title .date { font-size: 0.85rem; color: #64748b; font-weight: 400; }
  .no-data { color: #475569; font-size: 0.9rem; padding: 1rem 0; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1rem; }
  .card { background: #1e293b; border-radius: 12px; padding: 1.25rem; }
  .card h3 {
    font-size: 0.85rem; color: #64748b; margin-bottom: 0.75rem;
    text-transform: uppercase; letter-spacing: 0.03em;
  }
  .metrics { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 0.5rem; }
  .metric { padding: 0.6rem; background: #0f172a; border-radius: 8px; }
  .metric .val { font-size: 1.25rem; font-weight: 700; }
  .metric .lbl { font-size: 0.7rem; color: #64748b; margin-top: 0.15rem; }
  .score-ring {
    display: inline-flex; align-items: center; justify-content: center;
    width: 56px; height: 56px; border-radius: 50%; font-size: 1.3rem; font-weight: 700;
  }
  .score-row { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.75rem; }
  .score-label { font-size: 0.8rem; color: #94a3b8; }
  .contrib-bar { height: 6px; border-radius: 3px; background: #334155; margin-top: 0.25rem; }
  .contrib-fill { height: 100%; border-radius: 3px; }
  .contrib-item { margin-bottom: 0.5rem; }
  .contrib-head { display: flex; justify-content: space-between; font-size: 0.75rem; color: #94a3b8; }
  canvas { width: 100% !important; }
  .error-banner {
    background: #7f1d1d; border-radius: 8px; padding: 1rem; margin-bottom: 1rem;
    display: none; font-size: 0.875rem;
  }

  /* Heart rate chart */
  #hr-section { margin-bottom: 2rem; }
  #hr-section .section-title { font-size: 1.15rem; font-weight: 600; margin-bottom: 0.75rem; }
  #hr-main-wrap { background: #1e293b; border-radius: 12px 12px 0 0; padding: 1rem 1rem 0.5rem; }
  #hr-nav-wrap { background: #1e293b; border-radius: 0 0 12px 12px; padding: 0 1rem 0.75rem; position: relative; }
  .uplot { display: block; margin: 0 auto; }

  /* Navigator overlay */
  .nav-overlay {
    position: absolute; pointer-events: none;
  }
  .nav-curtain {
    position: absolute; top: 0; height: 100%;
    background: rgba(15,23,42,0.55); pointer-events: all; cursor: pointer;
  }
  .nav-curtain-l { left: 0; }
  .nav-curtain-r { right: 0; }
  .nav-sel {
    position: absolute; top: 0; height: 100%;
    pointer-events: all; cursor: grab;
    border-left: 1px solid rgba(148,163,184,0.4);
    border-right: 1px solid rgba(148,163,184,0.4);
  }
  .nav-sel:active { cursor: grabbing; }
  .nav-handle {
    position: absolute; top: 50%; transform: translateY(-50%);
    width: 14px; height: 28px;
    background: #475569; border: 1px solid #64748b; border-radius: 3px;
    cursor: ew-resize; pointer-events: all;
    display: flex; align-items: center; justify-content: center;
  }
  .nav-handle::after {
    content: ''; display: block;
    width: 4px; height: 10px;
    border-left: 1px solid #94a3b8; border-right: 1px solid #94a3b8;
  }
  .nav-handle-l { left: -7px; }
  .nav-handle-r { right: -7px; }
</style>
</head>
<body>
<div class="header">
  <h1>Health Dashboard</h1>
</div>
<div class="error-banner" id="errorBanner"></div>
<div id="hr-section">
  <div class="section-title">Heart Rate</div>
  <div id="hr-main-wrap"></div>
  <div id="hr-nav-wrap"></div>
</div>
<div id="app"></div>

<script>
const C = { indigo:'#6366f1', cyan:'#06b6d4', emerald:'#10b981', amber:'#f59e0b',
            rose:'#f43f5e', purple:'#a855f7', slate:'#64748b', sky:'#38bdf8' };
const chartOpts = {
  responsive: true,
  plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
  scales: {
    x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e293b' } },
    y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: '#1e293b' } },
  },
};

async function fetchJSON(u) { return (await fetch(u)).json(); }
function sv(obj) { return obj ? obj.value : null; }
function durMin(obj) { return obj ? obj.value : 0; }
function toHM(s) {
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
}
function scoreColor(v) { return v >= 85 ? C.emerald : v >= 70 ? C.amber : C.rose; }

function getToday() {
  const now = new Date();
  const pac = new Date(now.toLocaleString('en-US', { timeZone: 'America/Los_Angeles' }));
  if (pac.getHours() < 3) pac.setDate(pac.getDate() - 1);
  return pac.toISOString().slice(0, 10);
}
const DAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
function fmtDate(iso) {
  const d = new Date(iso + 'T12:00:00');
  return DAYS[d.getDay()] + ', ' + d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function showError(msg) {
  const el = document.getElementById('errorBanner');
  el.textContent = msg;
  el.style.display = 'block';
}

function contribBar(label, val, color) {
  if (val == null) return '';
  return '<div class="contrib-item"><div class="contrib-head"><span>' + label +
    '</span><span>' + Math.round(val) + '</span></div><div class="contrib-bar"><div class="contrib-fill" style="width:' +
    val + '%;background:' + color + '"></div></div></div>';
}

async function loadDashboard() {
  let sessions, readinessTsResp, sleepScoreTsResp, tempDevResp, tempTrendResp;
  try {
    [sessions, readinessTsResp, sleepScoreTsResp, tempDevResp, tempTrendResp] = await Promise.all([
      fetchJSON('/api/sleep-sessions?limit=60'),
      fetchJSON('/api/time-series?metric=readiness_score'),
      fetchJSON('/api/time-series?metric=sleep_score'),
      fetchJSON('/api/time-series?metric=temperature_deviation'),
      fetchJSON('/api/time-series?metric=temperature_trend_deviation'),
    ]);
  } catch (e) {
    showError('Could not load health data. Make sure a health-data provider app is installed and the service permissions are granted.');
    return;
  }

  const readinessContribMetrics = [
    'readiness_activity_balance','readiness_body_temperature','readiness_hrv_balance',
    'readiness_previous_day_activity','readiness_previous_night','readiness_recovery_index',
    'readiness_resting_heart_rate','readiness_sleep_balance','readiness_sleep_regularity',
  ];
  const sleepScoreContribMetrics = [
    'sleep_score_deep_sleep','sleep_score_efficiency','sleep_score_latency',
    'sleep_score_rem_sleep','sleep_score_restfulness','sleep_score_timing','sleep_score_total_sleep',
  ];

  const allSess = sessions.data.slice().reverse();
  const sess = allSess.filter(s => durMin(s.total_duration) >= 30);
  const today = getToday();

  const lastReal = sessions.data.find(s => durMin(s.total_duration) >= 30);
  let lastNightDay = null;
  if (lastReal) {
    const end = new Date(lastReal.end);
    const endPac = new Date(end.toLocaleString('en-US', { timeZone: 'America/Los_Angeles' }));
    if (endPac.getHours() < 3) endPac.setDate(endPac.getDate() - 1);
    lastNightDay = endPac.toISOString().slice(0, 10);
  }

  const readinessMap = {}, sleepScoreMap = {}, tempDevMap = {}, tempTrendMap = {};
  (readinessTsResp.samples || []).forEach(s => { readinessMap[s.timestamp.slice(0, 10)] = s.value; });
  (sleepScoreTsResp.samples || []).forEach(s => { sleepScoreMap[s.timestamp.slice(0, 10)] = s.value; });
  (tempDevResp.samples || []).forEach(s => { tempDevMap[s.timestamp.slice(0, 10)] = s.value; });
  (tempTrendResp.samples || []).forEach(s => { tempTrendMap[s.timestamp.slice(0, 10)] = s.value; });

  const todayContribs = {};
  if (readinessMap[today] != null) {
    const contribResults = await Promise.all(
      readinessContribMetrics.map(m => fetchJSON('/api/time-series?metric=' + m))
    );
    readinessContribMetrics.forEach((m, i) => {
      const samples = contribResults[i].samples || [];
      const todaySample = samples.find(s => s.timestamp.slice(0, 10) === today);
      if (todaySample) todayContribs[m] = todaySample.value;
    });
  }

  const sleepContribs = {};
  if (lastNightDay && sleepScoreMap[lastNightDay] != null) {
    const contribResults = await Promise.all(
      sleepScoreContribMetrics.map(m => fetchJSON('/api/time-series?metric=' + m))
    );
    sleepScoreContribMetrics.forEach((m, i) => {
      const samples = contribResults[i].samples || [];
      const daySample = samples.find(s => s.timestamp.slice(0, 10) === lastNightDay);
      if (daySample) sleepContribs[m] = daySample.value;
    });
  }

  let html = '';

  // ---- LAST NIGHT'S SLEEP ----
  html += '<section id="last-night">';
  if (lastReal) {
    const isToday = lastNightDay === today;
    html += '<div class="section-title">Last Night\\'s Sleep <span class="date">' +
      fmtDate(lastNightDay) + (isToday ? '' : ' (not current)') + '</span></div>';
    const sleepScore = sv(lastReal.sleep_score) || sleepScoreMap[lastNightDay];

    html += '<div class="grid">';

    // Sleep stats card
    html += '<div class="card"><h3>Sleep Summary</h3>';
    if (sleepScore != null) {
      html += '<div class="score-row"><div class="score-ring" style="border:3px solid ' +
        scoreColor(sleepScore) + '">' + Math.round(sleepScore) +
        '</div><div><div style="font-weight:600">Sleep Score</div><div class="score-label">Total: ' +
        toHM(durMin(lastReal.total_duration) * 60) + '</div></div></div>';
    }
    html += '<div class="metrics">';
    const stats = [
      ['Total Sleep', toHM(durMin(lastReal.total_duration) * 60)],
      ['Deep', toHM(durMin(lastReal.deep_sleep_duration) * 60)],
      ['REM', toHM(durMin(lastReal.rem_sleep_duration) * 60)],
      ['Light', toHM(durMin(lastReal.light_sleep_duration) * 60)],
      ['Awake', toHM(durMin(lastReal.awake_time) * 60)],
      ['Time in Bed', toHM(durMin(lastReal.time_in_bed) * 60)],
      ['Avg HR', sv(lastReal.average_heart_rate) != null ? Math.round(sv(lastReal.average_heart_rate)) + ' bpm' : '--'],
      ['Lowest HR', sv(lastReal.lowest_heart_rate) != null ? Math.round(sv(lastReal.lowest_heart_rate)) + ' bpm' : '--'],
      ['Avg HRV', sv(lastReal.average_hrv) != null ? Math.round(sv(lastReal.average_hrv)) + ' ms' : '--'],
      ['Avg Breath', sv(lastReal.average_breath) != null ? sv(lastReal.average_breath).toFixed(1) + '/min' : '--'],
      ['Efficiency', sv(lastReal.efficiency) != null ? Math.round(sv(lastReal.efficiency)) + '%' : '--'],
      ['Latency', lastReal.latency ? toHM(durMin(lastReal.latency) * 60) : '--'],
    ];
    stats.forEach(function(s) {
      html += '<div class="metric"><div class="val">' + s[1] + '</div><div class="lbl">' + s[0] + '</div></div>';
    });
    html += '</div>';

    const contribs = [
      ['Deep Sleep','sleep_score_deep_sleep'], ['REM Sleep','sleep_score_rem_sleep'],
      ['Total Sleep','sleep_score_total_sleep'], ['Efficiency','sleep_score_efficiency'],
      ['Restfulness','sleep_score_restfulness'], ['Latency','sleep_score_latency'],
      ['Timing','sleep_score_timing'],
    ];
    const hasContribs = contribs.some(function(c) { return sleepContribs[c[1]] != null; });
    if (hasContribs) {
      html += '<h3 style="margin-top:1rem">Score Breakdown</h3>';
      contribs.forEach(function(c) { html += contribBar(c[0], sleepContribs[c[1]], C.purple); });
    }
    html += '</div>';

    // Charts card
    html += '<div class="card"><h3>Sleep Stages</h3><canvas id="lastStages"></canvas>';
    html += '<h3 style="margin-top:1rem">Heart Rate</h3><canvas id="lastHr"></canvas>';
    html += '<h3 style="margin-top:1rem">HRV</h3><canvas id="lastHrv"></canvas>';
    html += '</div>';

    html += '</div>';
  } else {
    html += '<div class="section-title">Last Night\\'s Sleep</div><div class="no-data">No sleep data available</div>';
  }
  html += '</section>';

  // ---- TODAY ----
  html += '<section id="today">';
  const todayReadiness = readinessMap[today];
  if (todayReadiness != null) {
    html += '<div class="section-title">Today <span class="date">' + fmtDate(today) + '</span></div>';
    html += '<div class="grid">';

    html += '<div class="card"><h3>Readiness</h3>';
    html += '<div class="score-row"><div class="score-ring" style="border:3px solid ' +
      scoreColor(todayReadiness) + '">' + Math.round(todayReadiness) +
      '</div><div style="font-weight:600">Readiness Score</div></div>';
    const rContribs = [
      ['Resting HR','readiness_resting_heart_rate'], ['HRV Balance','readiness_hrv_balance'],
      ['Body Temperature','readiness_body_temperature'], ['Recovery Index','readiness_recovery_index'],
      ['Previous Night','readiness_previous_night'], ['Sleep Balance','readiness_sleep_balance'],
      ['Activity Balance','readiness_activity_balance'], ['Sleep Regularity','readiness_sleep_regularity'],
    ];
    rContribs.forEach(function(c) { html += contribBar(c[0], todayContribs[c[1]], C.emerald); });
    html += '</div>';

    html += '<div class="card"><h3>Body Signals</h3><div class="metrics">';
    const tempDev = tempDevMap[today];
    const tempTrend = tempTrendMap[today];
    if (tempDev != null) {
      html += '<div class="metric"><div class="val">' + (tempDev > 0 ? '+' : '') +
        tempDev.toFixed(2) + '&deg;</div><div class="lbl">Temp Deviation</div></div>';
    }
    if (tempTrend != null) {
      html += '<div class="metric"><div class="val">' + (tempTrend > 0 ? '+' : '') +
        tempTrend.toFixed(2) + '&deg;</div><div class="lbl">Temp Trend</div></div>';
    }
    html += '</div></div>';

    html += '</div>';
  } else {
    html += '<div class="section-title">Today <span class="date">' + fmtDate(today) +
      '</span></div><div class="no-data">No data for today yet.</div>';
  }
  html += '</section>';

  // ---- HISTORY ----
  html += '<section id="history">';
  html += '<div class="section-title">History</div>';
  html += '<div class="grid">';
  html += '<div class="card"><h3>Sleep Duration</h3><canvas id="sleepDuration"></canvas></div>';
  html += '<div class="card"><h3>Readiness &amp; Sleep Score</h3><canvas id="scores"></canvas></div>';
  html += '<div class="card"><h3>Sleeping Heart Rate</h3><canvas id="hrChart"></canvas></div>';
  html += '<div class="card"><h3>Sleeping HRV</h3><canvas id="hrvChart"></canvas></div>';
  html += '</div></section>';

  document.getElementById('app').innerHTML = html;

  // ---- RENDER CHARTS ----

  // Last night charts (embedded in sleep session response)
  if (lastReal) {
    const timeFmt = function(d) {
      return new Date(d.timestamp).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Los_Angeles' });
    };

    const stageData = lastReal.stages;
    if (stageData && stageData.samples && stageData.samples.length > 0) {
      const sColors = { deep: C.indigo, light: C.slate, rem: C.cyan, awake: C.amber };
      const sNums = { deep: 1, light: 2, rem: 3, awake: 4 };
      new Chart(document.getElementById('lastStages'), {
        type: 'bar',
        data: {
          labels: stageData.samples.map(timeFmt),
          datasets: [{
            data: stageData.samples.map(function(d) { return sNums[d.value] || 0; }),
            backgroundColor: stageData.samples.map(function(d) { return sColors[d.value] || '#64748b'; }),
            barPercentage: 1, categoryPercentage: 1,
          }],
        },
        options: {
          ...chartOpts,
          plugins: { ...chartOpts.plugins, legend: { display: false },
            tooltip: { callbacks: { label: function(c) {
              const rev = { 1: 'Deep', 2: 'Light', 3: 'REM', 4: 'Awake' };
              return rev[c.raw] || c.raw;
            } } },
          },
          scales: { ...chartOpts.scales,
            y: { ...chartOpts.scales.y, min: 0.5, max: 4.5,
              ticks: { ...chartOpts.scales.y.ticks, callback: function(v) {
                const m = { 1: 'Deep', 2: 'Light', 3: 'REM', 4: 'Awake' };
                return m[v] || '';
              } },
            },
          },
        },
      });
    }

    const hrData = lastReal.heart_rate;
    if (hrData && hrData.samples && hrData.samples.length > 0) {
      new Chart(document.getElementById('lastHr'), {
        type: 'line',
        data: {
          labels: hrData.samples.map(timeFmt),
          datasets: [{
            label: 'bpm', data: hrData.samples.map(function(d) { return d.value; }),
            borderColor: C.rose, tension: 0.3, pointRadius: 0, borderWidth: 1.5,
          }],
        },
        options: { ...chartOpts, plugins: { ...chartOpts.plugins, legend: { display: false } } },
      });
    }

    const hrvData = lastReal.hrv;
    if (hrvData && hrvData.samples && hrvData.samples.length > 0) {
      new Chart(document.getElementById('lastHrv'), {
        type: 'line',
        data: {
          labels: hrvData.samples.map(timeFmt),
          datasets: [{
            label: 'ms', data: hrvData.samples.map(function(d) { return d.value; }),
            borderColor: C.cyan, tension: 0.3, pointRadius: 0, borderWidth: 1.5,
            fill: true, backgroundColor: 'rgba(6,182,212,0.08)',
          }],
        },
        options: { ...chartOpts, plugins: { ...chartOpts.plugins, legend: { display: false } } },
      });
    }
  }

  // History charts
  if (sess.length > 0) {
    const labels = sess.map(function(s) { return s.start.slice(5, 10); });
    new Chart(document.getElementById('sleepDuration'), {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          { label: 'Deep', data: sess.map(function(s) { return +(durMin(s.deep_sleep_duration) / 60).toFixed(1); }), backgroundColor: C.indigo },
          { label: 'REM', data: sess.map(function(s) { return +(durMin(s.rem_sleep_duration) / 60).toFixed(1); }), backgroundColor: C.cyan },
          { label: 'Light', data: sess.map(function(s) { return +(durMin(s.light_sleep_duration) / 60).toFixed(1); }), backgroundColor: C.slate },
        ],
      },
      options: { ...chartOpts,
        scales: { ...chartOpts.scales,
          x: { ...chartOpts.scales.x, stacked: true },
          y: { ...chartOpts.scales.y, stacked: true },
        },
      },
    });

    new Chart(document.getElementById('hrChart'), {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          { label: 'Avg', data: sess.map(function(s) { return sv(s.average_heart_rate); }), borderColor: C.rose, tension: 0.3, pointRadius: 2, spanGaps: true },
          { label: 'Low', data: sess.map(function(s) { return sv(s.lowest_heart_rate); }), borderColor: C.amber, tension: 0.3, pointRadius: 2, spanGaps: true },
        ],
      },
      options: chartOpts,
    });

    new Chart(document.getElementById('hrvChart'), {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Avg HRV', data: sess.map(function(s) { return sv(s.average_hrv); }),
          borderColor: C.cyan, tension: 0.3, pointRadius: 2, spanGaps: true,
          fill: true, backgroundColor: 'rgba(6,182,212,0.08)',
        }],
      },
      options: chartOpts,
    });
  }

  // Scores chart
  const allDates = [...new Set([...Object.keys(readinessMap), ...Object.keys(sleepScoreMap)])].sort();
  if (allDates.length > 0) {
    new Chart(document.getElementById('scores'), {
      type: 'line',
      data: {
        labels: allDates.map(function(d) { return d.slice(5); }),
        datasets: [
          { label: 'Readiness', data: allDates.map(function(d) { return readinessMap[d] ?? null; }), borderColor: C.emerald, tension: 0.3, pointRadius: 2, spanGaps: true },
          { label: 'Sleep', data: allDates.map(function(d) { return sleepScoreMap[d] ?? null; }), borderColor: C.purple, tension: 0.3, pointRadius: 2, spanGaps: true },
        ],
      },
      options: { ...chartOpts,
        scales: { ...chartOpts.scales, y: { ...chartOpts.scales.y, min: 50, max: 100 } },
      },
    });
  }
}

// ---- Heart Rate Explorer (uPlot + custom navigator) ----
(function() {
  var ROSE = '#f43f5e';
  var GAP_THRESHOLD = 600; // 10 min — treat gaps larger than this as missing data
  var nowSec = Math.floor(Date.now() / 1000);
  var t24h = nowSec - 24 * 3600;
  var t3h  = nowSec - 3 * 3600;
  var startISO = new Date(t24h * 1000).toISOString();

  fetch('/api/time-series?metric=heart_rate&start=' + startISO)
    .then(function(r) { return r.json(); })
    .then(function(resp) { renderHR(resp.samples || []); })
    .catch(function() {});

  function renderHR(samples) {
    if (samples.length === 0) {
      document.getElementById('hr-section').style.display = 'none';
      return;
    }

    // Build arrays with nulls inserted at gaps so the line breaks
    var rawTs = [], rawVals = [];
    for (var i = 0; i < samples.length; i++) {
      var t = new Date(samples[i].timestamp).getTime() / 1000;
      if (i > 0 && t - rawTs[rawTs.length - 1] > GAP_THRESHOLD) {
        // Insert a null point midway through the gap to break the line
        rawTs.push((rawTs[rawTs.length - 1] + t) / 2);
        rawVals.push(null);
      }
      rawTs.push(t);
      rawVals.push(samples[i].value);
    }
    var data = [rawTs, rawVals];

    var mainWrap = document.getElementById('hr-main-wrap');
    var navWrap = document.getElementById('hr-nav-wrap');
    var width = mainWrap.clientWidth - 16;

    var uMain, uNav;
    var selLeft = 0, selWidth = 0;

    function selToScale() {
      var min = uNav.posToVal(selLeft, 'x');
      var max = uNav.posToVal(selLeft + selWidth, 'x');
      uMain.setScale('x', { min: min, max: max });
    }

    // Becomes real after nav overlay is ready
    var scaleToSel = function() {};

    // --- Main chart ---
    uMain = new uPlot({
      width: width, height: 300, pxAlign: 0,
      cursor: { drag: { x: true, y: false } },
      select: { over: false },
      legend: { show: false },
      scales: {
        x: { min: t3h, max: nowSec },
        y: { auto: true, range: function(u, dMin, dMax) {
          if (dMin == null) return [40, 120];
          var pad = (dMax - dMin) * 0.1 || 5;
          return [Math.floor(dMin - pad), Math.ceil(dMax + pad)];
        }},
      },
      axes: [
        { stroke: '#64748b', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' }, font: '11px system-ui' },
        { stroke: '#64748b', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' }, font: '11px system-ui',
          values: function(u, vs) { return vs.map(function(v) { return v + ''; }); } },
      ],
      series: [ {},
        { stroke: ROSE, width: 1.5, fill: 'rgba(244,63,94,0.06)',
          spanGaps: false, label: 'bpm',
          points: { show: false } },
      ],
      hooks: { setScale: [ function() { if (uNav) scaleToSel(); } ] },
    }, data, mainWrap);

    // --- Navigator chart ---
    var navReady;
    var navReadyPromise = new Promise(function(resolve) { navReady = resolve; });

    uNav = new uPlot({
      width: width, height: 100, pxAlign: 0,
      cursor: { show: false, drag: { x: false, y: false } },
      legend: { show: false },
      scales: {
        x: { min: t24h, max: nowSec },
        y: { auto: true },
      },
      axes: [
        { stroke: '#64748b', grid: { show: false }, ticks: { stroke: '#334155' }, font: '10px system-ui', size: 28 },
        { show: false },
      ],
      series: [ {},
        { stroke: ROSE, width: 1, fill: 'rgba(244,63,94,0.04)',
          spanGaps: false, points: { show: false } },
      ],
      hooks: { ready: [ function() { navReady(); } ] },
    }, data, navWrap);

    // --- Custom overlay for navigator selection ---
    navReadyPromise.then(function() {
      var over = uNav.root.querySelector('.u-over');
      over.style.position = 'relative';
      over.style.overflow = 'visible';

      var overlay = document.createElement('div');
      overlay.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10;';

      var curtainL = document.createElement('div');
      curtainL.className = 'nav-curtain nav-curtain-l';
      var curtainR = document.createElement('div');
      curtainR.className = 'nav-curtain nav-curtain-r';
      var sel = document.createElement('div');
      sel.className = 'nav-sel';
      var handleL = document.createElement('div');
      handleL.className = 'nav-handle nav-handle-l';
      var handleR = document.createElement('div');
      handleR.className = 'nav-handle nav-handle-r';

      sel.appendChild(handleL);
      sel.appendChild(handleR);
      overlay.appendChild(curtainL);
      overlay.appendChild(sel);
      overlay.appendChild(curtainR);
      over.appendChild(overlay);

      function positionOverlay() {
        var maxW = over.clientWidth;
        var l = Math.max(0, Math.min(selLeft, maxW));
        var w = Math.max(20, Math.min(selWidth, maxW - l));
        curtainL.style.width = l + 'px';
        sel.style.left = l + 'px';
        sel.style.width = w + 'px';
        curtainR.style.left = (l + w) + 'px';
        curtainR.style.width = (maxW - l - w) + 'px';
      }

      // Initial selection: last 3 hours
      selLeft = Math.round(uNav.valToPos(t3h, 'x'));
      selWidth = Math.round(uNav.valToPos(nowSec, 'x')) - selLeft;
      positionOverlay();
      selToScale();

    // --- Drag interaction ---
    function startDrag(e, mode) {
      e.preventDefault();
      e.stopPropagation();
      var startX = e.clientX;
      var origL = selLeft, origW = selWidth;

      function onMove(e) {
        var dx = e.clientX - startX;
        var maxW = over.clientWidth;
        if (mode === 'pan') {
          var nl = origL + dx;
          if (nl < 0) nl = 0;
          if (nl + origW > maxW) nl = maxW - origW;
          selLeft = nl; selWidth = origW;
        } else if (mode === 'left') {
          var nl = origL + dx;
          var nr = origL + origW;
          if (nl < 0) nl = 0;
          if (nl > nr - 10) nl = nr - 10;
          selLeft = nl; selWidth = nr - nl;
        } else if (mode === 'right') {
          var nw = origW + dx;
          if (nw < 10) nw = 10;
          if (origL + nw > maxW) nw = maxW - origL;
          selLeft = origL; selWidth = nw;
        }
        positionOverlay();
        selToScale();
      }

      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    }

    sel.addEventListener('mousedown', function(e) {
      if (e.target === handleL || e.target === handleR) return;
      startDrag(e, 'pan');
    });
    handleL.addEventListener('mousedown', function(e) { startDrag(e, 'left'); });
    handleR.addEventListener('mousedown', function(e) { startDrag(e, 'right'); });

    curtainL.addEventListener('mousedown', function(e) {
      var rect = over.getBoundingClientRect();
      var clickX = e.clientX - rect.left;
      var newL = clickX - selWidth / 2;
      var maxW = over.clientWidth;
      if (newL < 0) newL = 0;
      if (newL + selWidth > maxW) newL = maxW - selWidth;
      selLeft = newL;
      positionOverlay();
      selToScale();
      startDrag(e, 'pan');
    });
    curtainR.addEventListener('mousedown', function(e) {
      var rect = over.getBoundingClientRect();
      var clickX = e.clientX - rect.left;
      var newL = clickX - selWidth / 2;
      var maxW = over.clientWidth;
      if (newL < 0) newL = 0;
      if (newL + selWidth > maxW) newL = maxW - selWidth;
      selLeft = newL;
      positionOverlay();
      selToScale();
      startDrag(e, 'pan');
    });

      // Wire up scaleToSel for main chart zoom -> nav sync
      scaleToSel = function() {
        selLeft = Math.round(uNav.valToPos(uMain.scales.x.min, 'x'));
        var r = Math.round(uNav.valToPos(uMain.scales.x.max, 'x'));
        selWidth = r - selLeft;
        positionOverlay();
      };
    }); // end navReadyPromise.then
  }
})();

loadDashboard();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

HEART_RATE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Heart Rate</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 1.5rem; max-width: 1100px; margin: 0 auto;
  }
  .header { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem; }
  .header a { color: #64748b; text-decoration: none; font-size: 0.85rem; }
  h1 { font-size: 1.5rem; }
  #main-wrap, #nav-wrap { background: #1e293b; border-radius: 12px; padding: 1rem; }
  #nav-wrap { margin-top: 0.75rem; }
  .loading { text-align: center; padding: 3rem; color: #64748b; }

  .uplot { display: block; margin: 0 auto; }
  .uplot .u-over { cursor: crosshair; }
  .u-axis { pointer-events: none; }

  /* Navigator selection + grips */
  #nav-wrap .u-select {
    pointer-events: all;
    cursor: grab;
    background: rgba(99,102,241,0.15);
    border-left: 2px solid #6366f1;
    border-right: 2px solid #6366f1;
  }
  #nav-wrap .u-select:active { cursor: grabbing; }
  .u-grip {
    position: absolute;
    width: 8px;
    height: 100%;
    top: 0;
    cursor: ew-resize;
    background: #6366f1;
    border-radius: 2px;
    opacity: 0.9;
  }
  .u-grip-l { left: -4px; }
  .u-grip-r { right: -4px; }

  /* Curtains over un-selected range */
  #nav-wrap .u-over { position: relative; }
  .nav-curtain {
    position: absolute;
    top: 0;
    height: 100%;
    background: rgba(15,23,42,0.6);
    pointer-events: none;
  }
</style>
</head>
<body>
<div class="header">
  <a href="/">&larr; Dashboard</a>
  <h1>Heart Rate</h1>
</div>
<div id="main-wrap"><div class="loading">Loading...</div></div>
<div id="nav-wrap"></div>

<script src="https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.js"></script>
<script>
(function() {
  const ROSE = '#f43f5e';

  function debounce(fn) {
    let raf;
    return function() {
      const args = arguments, self = this;
      if (raf) return;
      raf = requestAnimationFrame(function() { fn.apply(self, args); raf = null; });
    };
  }

  function placeDiv(par, cls) {
    const el = document.createElement('div');
    el.className = cls;
    par.appendChild(el);
    return el;
  }

  const now = Date.now() / 1000;
  const t24h = now - 24 * 3600;
  const t3h  = now - 3 * 3600;
  const startISO = new Date(t24h * 1000).toISOString();

  fetch('/api/time-series?metric=heart_rate&start=' + startISO)
    .then(function(r) { return r.json(); })
    .then(function(resp) { render(resp.samples || []); })
    .catch(function() {
      document.getElementById('main-wrap').innerHTML = '<div class="loading">Could not load heart rate data.</div>';
    });

  function render(samples) {
    if (samples.length === 0) {
      document.getElementById('main-wrap').innerHTML = '<div class="loading">No heart rate data in the last 24 hours.</div>';
      return;
    }

    const ts = new Float64Array(samples.length);
    const vals = new Float64Array(samples.length);
    for (let i = 0; i < samples.length; i++) {
      ts[i] = new Date(samples[i].timestamp).getTime() / 1000;
      vals[i] = samples[i].value;
    }
    const data = [ts, vals];

    const width = Math.min(document.getElementById('main-wrap').clientWidth - 32, 1050);

    document.getElementById('main-wrap').innerHTML = '';
    document.getElementById('nav-wrap').innerHTML = '';

    let uNav, uMain;
    let navLftCurtain, navRgtCurtain;

    // ---- Main (zoomed) chart ----
    const mainOpts = {
      width: width,
      height: 340,
      pxAlign: 0,
      cursor: {
        drag: { x: true, y: false },
      },
      select: { over: false },
      scales: {
        x: { min: t3h, max: now },
        y: { auto: true },
      },
      axes: [
        { stroke: '#64748b', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' }, font: '11px system-ui', },
        { stroke: '#64748b', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' }, font: '11px system-ui',
          values: function(u, vals) { return vals.map(function(v) { return v + ' bpm'; }); },
        },
      ],
      series: [
        {},
        { stroke: ROSE, width: 1.5, fill: 'rgba(244,63,94,0.08)', label: 'HR' },
      ],
      hooks: {
        setScale: [
          function(u) {
            if (!uNav) return;
            const xMin = u.scales.x.min;
            const xMax = u.scales.x.max;
            const left = Math.round(uNav.valToPos(xMin, 'x'));
            const right = Math.round(uNav.valToPos(xMax, 'x'));
            uNav.setSelect({ left: left, width: right - left, height: uNav.bbox.height / devicePixelRatio }, false);
            updateCurtains();
          },
        ],
      },
    };

    uMain = new uPlot(mainOpts, data, document.getElementById('main-wrap'));

    // ---- Navigator (ranger) chart ----
    function updateCurtains() {
      if (!navLftCurtain || !uNav) return;
      const sel = uNav.select;
      navLftCurtain.style.left = '0';
      navLftCurtain.style.width = sel.left + 'px';
      navRgtCurtain.style.left = (sel.left + sel.width) + 'px';
      navRgtCurtain.style.width = (uNav.bbox.width / devicePixelRatio - sel.left - sel.width) + 'px';
    }

    const BOUNDARY_LEFT = 0, BOUNDARY_RIGHT = 1, BOUNDARY_BOTH = 2;
    let x0, lft0, rgt0;

    function zoom(newLft, newWid) {
      const min = uNav.posToVal(newLft, 'x');
      const max = uNav.posToVal(newLft + newWid, 'x');
      uMain.setScale('x', { min: min, max: max });
    }

    function update(newLft, newRgt, boundary) {
      const maxRgt = uNav.bbox.width / devicePixelRatio;
      if (boundary === BOUNDARY_BOTH) {
        const w = newRgt - newLft;
        if (newRgt > maxRgt) { newRgt = maxRgt; newLft = newRgt - w; }
        else if (newLft < 0) { newLft = 0; newRgt = newLft + w; }
      } else {
        if (newLft > newRgt) {
          if (boundary === BOUNDARY_LEFT) newLft = newRgt;
          else newRgt = newLft;
        }
        newLft = Math.max(0, newLft);
        newRgt = Math.min(newRgt, maxRgt);
      }
      zoom(newLft, newRgt - newLft);
    }

    function bindMove(e, onMove) {
      x0 = e.clientX;
      lft0 = uNav.select.left;
      rgt0 = lft0 + uNav.select.width;
      const _onMove = debounce(onMove);
      function _onUp() {
        document.removeEventListener('mouseup', _onUp);
        document.removeEventListener('mousemove', _onMove);
      }
      document.addEventListener('mousemove', _onMove);
      document.addEventListener('mouseup', _onUp);
      e.stopPropagation();
    }

    const navOpts = {
      width: width,
      height: 80,
      pxAlign: 0,
      cursor: {
        x: false,
        y: false,
        points: { show: false },
        drag: { setScale: false, setSelect: true, x: true, y: false },
      },
      legend: { show: false },
      scales: { x: {}, y: { auto: true } },
      axes: [
        { stroke: '#64748b', grid: { show: false }, ticks: { stroke: '#334155' }, font: '10px system-ui' },
        { show: false },
      ],
      series: [
        {},
        { stroke: ROSE, width: 1, fill: 'rgba(244,63,94,0.06)' },
      ],
      hooks: {
        ready: [
          function(u) {
            const left = Math.round(u.valToPos(t3h, 'x'));
            const right = Math.round(u.valToPos(now, 'x'));
            const height = u.bbox.height / devicePixelRatio;
            u.setSelect({ left: left, width: right - left, height: height }, false);

            const over = u.root.querySelector('.u-over');
            navLftCurtain = placeDiv(over, 'nav-curtain');
            navRgtCurtain = placeDiv(over, 'nav-curtain');
            updateCurtains();

            const sel = u.root.querySelector('.u-select');
            sel.addEventListener('mousedown', function(e) {
              bindMove(e, function(e) { update(lft0 + (e.clientX - x0), rgt0 + (e.clientX - x0), BOUNDARY_BOTH); });
            });
            placeDiv(sel, 'u-grip u-grip-l').addEventListener('mousedown', function(e) {
              bindMove(e, function(e) { update(lft0 + (e.clientX - x0), rgt0, BOUNDARY_LEFT); });
            });
            placeDiv(sel, 'u-grip u-grip-r').addEventListener('mousedown', function(e) {
              bindMove(e, function(e) { update(lft0, rgt0 + (e.clientX - x0), BOUNDARY_RIGHT); });
            });
          },
        ],
        setSelect: [
          function(u) {
            zoom(u.select.left, u.select.width);
            updateCurtains();
          },
        ],
      },
    };

    uNav = new uPlot(navOpts, data, document.getElementById('nav-wrap'));
  }
})();
</script>
</body>
</html>"""


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
