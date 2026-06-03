"""Dashboard builder: renderiza estado del bot + Firestore en HTML.

Server-side rendering. Lee:
- /api/state (en-process directo)
- Firestore eolo-crop-theta-decisions/{today}/decisions (orden desc, last 50)
- Cloud Scheduler jobs (via google-cloud-scheduler client) si está disponible
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from loguru import logger


def _safe_firestore_decisions(limit: int = 50) -> list[dict]:
    """Last N decisions today, ordered by recorded_ts desc.

    Field is `recorded_ts` (initial sector write) — for LLM audit fields the
    docs also carry `llm_recorded_ts`. We use recorded_ts for ordering so
    every doc is included regardless of whether the LLM update fired.
    """
    try:
        from google.cloud import firestore
        db = firestore.Client()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        docs = (
            db.collection("eolo-crop-theta-decisions")
              .document(today)
              .collection("decisions")
              .order_by("recorded_ts", direction=firestore.Query.DESCENDING)
              .limit(limit)
              .stream()
        )
        out = []
        for d in docs:
            data = d.to_dict() or {}
            data["_id"] = d.id
            out.append(data)
        return out
    except Exception as e:
        logger.warning(f"[dashboard] firestore read failed: {e}")
        return []


def _aggregate_rule_citations(decisions: list[dict]) -> dict[str, int]:
    """Count tacit_rules_applied citations today.

    Prefers the LLM-side field `llm_tacit_rules_applied` (populated by the
    Track A audit write); falls back to legacy `tacit_rules_applied`.
    """
    counts: dict[str, int] = {}
    for d in decisions:
        rules = d.get("llm_tacit_rules_applied") or d.get("tacit_rules_applied") or []
        if isinstance(rules, list):
            for r in rules:
                if isinstance(r, str):
                    counts[r] = counts.get(r, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _aggregate_verdicts(decisions: list[dict]) -> dict[str, int]:
    """Count llm_verdict distribution. NO_LLM bucket for sector-only decisions."""
    counts: dict[str, int] = {}
    for d in decisions:
        v = d.get("llm_verdict") or "NO_LLM"
        counts[v] = counts.get(v, 0) + 1
    return counts


def _safe_scheduler_jobs() -> list[dict]:
    """List Cloud Scheduler jobs in us-east1 with next-run-time. Optional dep."""
    try:
        from google.cloud import scheduler_v1
        client = scheduler_v1.CloudSchedulerClient()
        parent = "projects/eolo-schwab-agent/locations/us-east1"
        jobs = []
        for job in client.list_jobs(parent=parent):
            name = job.name.split("/")[-1]
            if name.startswith("eolo-"):
                jobs.append({
                    "name": name,
                    "schedule": job.schedule,
                    "state": job.state.name,
                    "last_attempt": str(job.last_attempt_time) if job.last_attempt_time else None,
                    "user_update": str(job.user_update_time) if job.user_update_time else None,
                })
        return jobs
    except Exception as e:
        logger.warning(f"[dashboard] scheduler list failed: {e}")
        return []


def build_dashboard_data(api_state: dict) -> dict:
    """Aggregate everything for dashboard rendering."""
    decisions = _safe_firestore_decisions(limit=50)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "api_state": api_state,
        "decisions": decisions,
        "rule_citations": _aggregate_rule_citations(decisions),
        "verdicts": _aggregate_verdicts(decisions),
        "scheduler_jobs": _safe_scheduler_jobs(),
    }


def render_html(data: dict) -> str:
    """Self-contained HTML with Chart.js CDN. NO localStorage."""
    api = data["api_state"] or {}
    stats = api.get("stats", {}) or {}
    llm_metrics = stats.get("llm_metrics", {}) or {}
    llm_cache = stats.get("llm_cache", {}) or {}
    positions = api.get("positions") or []
    paper_trades = api.get("paper_trades") or []
    verdicts = data["verdicts"] or {}
    citations = data["rule_citations"] or {}
    decisions = data["decisions"] or []
    jobs = data["scheduler_jobs"] or []

    verdicts_json = json.dumps(verdicts)
    citations_top10 = dict(list(citations.items())[:10])
    citations_json = json.dumps(citations_top10)

    decision_rows = ""
    for d in decisions[:50]:
        ts = d.get("recorded_ts") or d.get("ts") or 0
        try:
            ts_str = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%H:%M:%S") if ts else "?"
        except (ValueError, OSError):
            ts_str = "?"
        ticker = d.get("ticker", "?")
        src = d.get("decision_source", "?")
        llm_v = d.get("llm_verdict") or "—"
        llm_c = d.get("llm_confidence") if d.get("llm_confidence") is not None else "—"
        dte = d.get("dte_target") if d.get("dte_target") is not None else (d.get("dte") if d.get("dte") is not None else "—")
        rules = d.get("llm_tacit_rules_applied") or d.get("tacit_rules_applied") or []
        rules_str = ", ".join(rules[:5]) if rules else "—"
        overrides = d.get("llm_safety_overrides") or d.get("safety_overrides") or []
        ovr_str = ", ".join(overrides) if overrides else "—"
        lat = d.get("sonnet_latency_ms") if d.get("sonnet_latency_ms") is not None else "—"
        row_class = "row-block" if llm_v == "BLOCK_HARD" else ("row-wait" if llm_v == "WAIT" else "row-action")
        decision_rows += f'<tr class="{row_class}"><td>{ts_str}</td><td>{ticker}</td><td>{src}</td><td>{llm_v}</td><td>{llm_c}</td><td>{dte}</td><td>{rules_str}</td><td>{ovr_str}</td><td>{lat}</td></tr>'

    position_rows = ""
    for p in positions[:20]:
        position_rows += f'<tr><td>{p.get("ticker","?")}</td><td>{p.get("strategy","?")}</td><td>{p.get("strike","?")}</td><td>{p.get("dte","?")}</td><td>{p.get("premium","?")}</td><td>{p.get("pnl","?")}</td><td>{p.get("status","?")}</td></tr>'

    citation_rows = "".join(f'<tr><td>{rid}</td><td>{cnt}</td></tr>' for rid, cnt in citations.items())

    job_rows = ""
    for j in jobs:
        job_rows += f'<tr><td>{j["name"]}</td><td>{j["schedule"]}</td><td>{j["state"]}</td><td>{j.get("last_attempt","—")}</td></tr>'

    lat_block = llm_metrics.get("latency_ms", {}) or {}
    latency_p50 = lat_block.get("p50", "?")
    latency_p95 = lat_block.get("p95", "?")
    latency_avg = lat_block.get("avg", "?")
    if isinstance(latency_avg, (int, float)):
        latency_avg = f"{latency_avg:.0f}"
    if isinstance(latency_p50, (int, float)):
        latency_p50 = f"{latency_p50:.0f}"
    if isinstance(latency_p95, (int, float)):
        latency_p95 = f"{latency_p95:.0f}"
    total_calls = llm_metrics.get("total_calls", 0)
    errors = llm_metrics.get("errors", {})
    errors_total = sum(errors.values()) if isinstance(errors, dict) else 0
    cost_est = llm_metrics.get("cost_estimate_usd", 0) or 0
    cache_size = llm_cache.get("size", 0)
    cache_hits = llm_cache.get("hits", 0)
    cache_misses = llm_cache.get("misses", 0)
    cache_total = cache_hits + cache_misses
    hit_rate = (cache_hits / cache_total * 100) if cache_total else 0

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Eolo Crop LLM Audit — {data["date"]}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; padding: 20px; background: #f5f5f7; color: #1d1d1f; }}
    h1 {{ margin: 0 0 6px 0; font-size: 24px; }}
    .subtitle {{ color: #6e6e73; font-size: 13px; margin-bottom: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 20px; }}
    .card {{ background: white; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
    .card h2 {{ margin: 0 0 12px 0; font-size: 14px; color: #6e6e73; text-transform: uppercase; letter-spacing: 0.5px; }}
    .metric {{ font-size: 28px; font-weight: 600; }}
    .metric-sub {{ font-size: 12px; color: #6e6e73; margin-top: 4px; }}
    .ok {{ color: #34c759; }}
    .warn {{ color: #ff9500; }}
    .bad {{ color: #ff3b30; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th {{ text-align: left; padding: 8px 6px; color: #6e6e73; font-weight: 500; border-bottom: 1px solid #e5e5ea; text-transform: uppercase; font-size: 10px; letter-spacing: 0.5px; }}
    td {{ padding: 6px; border-bottom: 1px solid #f0f0f3; }}
    .row-wait td {{ color: #8e8e93; }}
    .row-action td {{ color: #34c759; font-weight: 500; }}
    .row-block td {{ color: #ff3b30; }}
    .full-width {{ grid-column: 1 / -1; }}
    .chart-container {{ position: relative; height: 220px; }}
    .refresh-info {{ font-size: 11px; color: #8e8e93; margin-top: 16px; text-align: center; }}
  </style>
</head>
<body>
  <h1>🌬️ Eolo Crop LLM Audit</h1>
  <div class="subtitle">Generado {data["generated_at"]} · Sesión {data["date"]}</div>

  <div class="grid">
    <div class="card">
      <h2>LLM Calls Hoy</h2>
      <div class="metric">{total_calls}</div>
      <div class="metric-sub">{errors_total} errores · ${cost_est:.2f} estimado</div>
    </div>
    <div class="card">
      <h2>Latency Sonnet</h2>
      <div class="metric">{latency_avg}ms</div>
      <div class="metric-sub">p50={latency_p50} · p95={latency_p95}</div>
    </div>
    <div class="card">
      <h2>Cache LLM</h2>
      <div class="metric">{cache_size}</div>
      <div class="metric-sub">{hit_rate:.0f}% hit rate ({cache_hits}/{cache_total})</div>
    </div>
    <div class="card">
      <h2>Posiciones Abiertas</h2>
      <div class="metric">{len(positions)}</div>
      <div class="metric-sub">{len(paper_trades)} paper trades hoy</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Verdict Distribution</h2>
      <div class="chart-container"><canvas id="verdictChart"></canvas></div>
    </div>
    <div class="card">
      <h2>Top 10 Reglas Citadas</h2>
      <div class="chart-container"><canvas id="citationChart"></canvas></div>
    </div>
  </div>

  <div class="card full-width">
    <h2>Decisiones Hoy (últimas {min(50, len(decisions))})</h2>
    <table>
      <thead><tr><th>UTC</th><th>Ticker</th><th>Source</th><th>Verdict</th><th>Conf</th><th>DTE</th><th>Reglas</th><th>Safety</th><th>Latency</th></tr></thead>
      <tbody>{decision_rows or '<tr><td colspan="9" style="color:#8e8e93">Sin decisiones aún</td></tr>'}</tbody>
    </table>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Posiciones</h2>
      <table>
        <thead><tr><th>Ticker</th><th>Strategy</th><th>Strike</th><th>DTE</th><th>Premium</th><th>P/L</th><th>Status</th></tr></thead>
        <tbody>{position_rows or '<tr><td colspan="7" style="color:#8e8e93">Sin posiciones abiertas</td></tr>'}</tbody>
      </table>
    </div>
    <div class="card">
      <h2>Citaciones KB v1.3</h2>
      <table>
        <thead><tr><th>Regla</th><th>Citas</th></tr></thead>
        <tbody>{citation_rows or '<tr><td colspan="2" style="color:#8e8e93">Sin citaciones aún</td></tr>'}</tbody>
      </table>
    </div>
  </div>

  <div class="card full-width">
    <h2>Cloud Scheduler Jobs</h2>
    <table>
      <thead><tr><th>Nombre</th><th>Schedule</th><th>State</th><th>Last Attempt</th></tr></thead>
      <tbody>{job_rows or '<tr><td colspan="4" style="color:#8e8e93">Sin jobs visibles</td></tr>'}</tbody>
    </table>
  </div>

  <div class="refresh-info">Refresh: recargá esta página para ver datos frescos</div>

  <script>
    const verdicts = {verdicts_json};
    const citations = {citations_json};
    new Chart(document.getElementById('verdictChart'), {{
      type: 'doughnut',
      data: {{
        labels: Object.keys(verdicts),
        datasets: [{{ data: Object.values(verdicts), backgroundColor: ['#8e8e93','#34c759','#007aff','#ff9500','#ff3b30','#5856d6'] }}]
      }},
      options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'right' }} }} }}
    }});
    new Chart(document.getElementById('citationChart'), {{
      type: 'bar',
      data: {{
        labels: Object.keys(citations),
        datasets: [{{ data: Object.values(citations), backgroundColor: '#007aff' }}]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false, indexAxis: 'y',
        plugins: {{ legend: {{ display: false }} }},
        scales: {{ x: {{ beginAtZero: true, ticks: {{ stepSize: 1 }} }} }}
      }}
    }});
  </script>
</body>
</html>"""
