# ============================================================
#  Theta Harvest Backtester — HTML Report Generator v2
#
#  Dashboard diseñado para leer la estrategia de un vistazo:
#    1. KPIs globales (P&L, WR, PF, Sharpe, MaxDD)
#    2. Equity curve acumulada
#    3. Panel de tranches (T0/T1/T2) — clave para paper trading
#    4. Análisis por DTE (tabla + bar chart)
#    5. Exit P&L contribution (impacto real de cada motivo de salida)
#    6. Risk zones + PUT vs CALL
#    7. Tabla de trades completa con tranche info
# ============================================================
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from .config import BACKTEST_START, BACKTEST_END, TARGET_DTES, TRANCHE_PROFIT_TARGETS, STOP_LOSS_MULT


def _safe_json(obj) -> str:
    return json.dumps(obj, default=str)


def generate_html_report(
    all_metrics: dict,
    all_results: Optional[dict] = None,
    output_path: Optional[Path] = None,
) -> Path:
    if output_path is None:
        output_path = Path(__file__).parent / "results" / "report.html"
    output_path.parent.mkdir(exist_ok=True)

    tickers = list(all_metrics.keys())
    tickers_with_data = [t for t in tickers if "error" not in all_metrics[t]]
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Config summary for header
    tranche_labels = " / ".join(
        f"T{i}={'EXPIRY' if t is None else str(int(t*100))+'%'}"
        for i, t in enumerate(TRANCHE_PROFIT_TARGETS)
    )
    config_str = (
        f"DTEs: {TARGET_DTES} &nbsp;|&nbsp; "
        f"SL: {int(STOP_LOSS_MULT*100)}% crédito &nbsp;|&nbsp; "
        f"Tranches: {tranche_labels}"
    )

    # Ticker colors
    COLORS = {
        "SPY":  {"border": "#3b82f6", "bg": "rgba(59,130,246,0.15)", "badge": "badge-spy"},
        "TQQQ": {"border": "#ea580c", "bg": "rgba(234,88,12,0.15)",  "badge": "badge-tqqq"},
    }
    TRANCHE_COLORS = ["#f59e0b", "#3b82f6", "#22c55e"]  # T0=amber, T1=blue, T2=green

    # ── Build datasets ────────────────────────────────────
    equity_datasets = []
    for ticker in tickers_with_data:
        pts = all_metrics[ticker].get("cumulative_pnl", [])
        c = COLORS.get(ticker, {"border": "#888", "bg": "rgba(128,128,128,0.1)"})
        equity_datasets.append({
            "label": ticker,
            "data": [{"x": p["date"], "y": p["cum"]} for p in pts],
            "borderColor": c["border"],
            "backgroundColor": c["bg"],
            "fill": False,
            "tension": 0.15,
            "pointRadius": 0,
            "borderWidth": 2,
        })

    all_dtes = sorted({d for t in tickers_with_data for d in all_metrics[t].get("by_dte", {}).keys()})

    # Exit P&L data (positive = profit, negative = loss)
    exit_chart_data = {}
    for ticker in tickers_with_data:
        bye = all_metrics[ticker].get("by_exit", {})
        # Sort by absolute P&L impact
        items = sorted(bye.items(), key=lambda x: abs(x[1]["total_pnl"]), reverse=True)
        exit_chart_data[ticker] = {
            "labels": [k for k, _ in items],
            "pnls":   [round(v["total_pnl"], 0) for _, v in items],
            "counts": [v["count"] for _, v in items],
            "colors": [
                "rgba(34,197,94,0.8)" if v["total_pnl"] >= 0 else "rgba(239,68,68,0.8)"
                for _, v in items
            ],
        }

    # ── HTML start ────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Theta Harvest — Backtest Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/chartjs-adapter-date-fns/3.0.0/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
:root {{
  --bg:      #0d0f18;
  --surface: #141722;
  --surface2:#1c2030;
  --border:  #252940;
  --text:    #e2e8f0;
  --muted:   #64748b;
  --muted2:  #94a3b8;
  --green:   #22c55e;
  --red:     #ef4444;
  --blue:    #3b82f6;
  --orange:  #f97316;
  --amber:   #f59e0b;
  --purple:  #a78bfa;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  font-size: 13px;
  line-height: 1.5;
  padding: 28px 32px;
  max-width: 1400px;
  margin: 0 auto;
}}

/* ── Header ── */
.header {{
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding-bottom: 22px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 28px;
}}
.header-title {{ font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }}
.header-sub {{ font-size: 12px; color: var(--muted2); margin-top: 4px; }}
.header-config {{
  font-size: 11px; color: var(--muted);
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 12px;
  margin-top: 8px;
  display: inline-block;
}}
.gen-time {{ font-size: 11px; color: var(--muted); text-align: right; }}

/* ── Section ── */
.section {{ margin-bottom: 28px; }}
.section-label {{
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--muted);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}}
.section-label::after {{
  content: '';
  flex: 1;
  height: 1px;
  background: var(--border);
}}

/* ── Cards ── */
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
}}
.card-title {{
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted2);
  margin-bottom: 14px;
}}

/* ── Grid layouts ── */
.grid-2  {{ display: grid; grid-template-columns: 1fr 1fr;         gap: 14px; }}
.grid-3  {{ display: grid; grid-template-columns: 1fr 1fr 1fr;     gap: 14px; }}
.grid-4  {{ display: grid; grid-template-columns: repeat(4, 1fr);  gap: 12px; }}
.grid-5  {{ display: grid; grid-template-columns: repeat(5, 1fr);  gap: 10px; }}
.grid-7  {{ display: grid; grid-template-columns: repeat(7, 1fr);  gap: 10px; }}
.mb14    {{ margin-bottom: 14px; }}

/* ── KPI boxes ── */
.kpi {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
}}
.kpi-label {{
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin-bottom: 6px;
}}
.kpi-value {{
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1;
}}
.kpi-sub {{ font-size: 11px; color: var(--muted2); margin-top: 4px; }}
.green  {{ color: var(--green); }}
.red    {{ color: var(--red); }}
.blue   {{ color: var(--blue); }}
.amber  {{ color: var(--amber); }}
.purple {{ color: var(--purple); }}
.orange {{ color: var(--orange); }}

/* ── Tranche cards ── */
.tranche-card {{
  border-radius: 10px;
  padding: 18px 20px;
  border: 1px solid var(--border);
  position: relative;
  overflow: hidden;
}}
.tranche-card::before {{
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
}}
.tranche-0::before {{ background: var(--amber); }}
.tranche-1::before {{ background: var(--blue); }}
.tranche-2::before {{ background: var(--green); }}
.tranche-0 {{ background: rgba(245,158,11,0.05); }}
.tranche-1 {{ background: rgba(59,130,246,0.05); }}
.tranche-2 {{ background: rgba(34,197,94,0.05); }}
.tranche-badge {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-bottom: 12px;
}}
.tb-0 {{ background: rgba(245,158,11,0.15); color: var(--amber); }}
.tb-1 {{ background: rgba(59,130,246,0.15); color: var(--blue); }}
.tb-2 {{ background: rgba(34,197,94,0.15);  color: var(--green); }}
.tranche-pnl {{ font-size: 28px; font-weight: 800; letter-spacing: -0.02em; line-height: 1; }}
.tranche-meta {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }}
.tranche-meta-item {{ background: rgba(255,255,255,0.04); border-radius: 6px; padding: 8px 10px; }}
.tranche-meta-label {{ font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
.tranche-meta-value {{ font-size: 15px; font-weight: 700; margin-top: 2px; }}

/* ── DTE table ── */
.dte-table {{ width: 100%; border-collapse: collapse; }}
.dte-table th {{
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--muted);
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
  text-align: left;
}}
.dte-table td {{
  padding: 10px 12px;
  border-bottom: 1px solid rgba(255,255,255,0.04);
  vertical-align: middle;
}}
.dte-table tr:last-child td {{ border-bottom: none; }}
.dte-table tr:hover td {{ background: rgba(255,255,255,0.025); }}
.dte-badge {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px; height: 32px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 800;
  background: var(--surface2);
  border: 1px solid var(--border);
}}
.pf-bar-wrap {{ background: var(--surface2); border-radius: 4px; height: 6px; overflow: hidden; }}
.pf-bar      {{ height: 100%; border-radius: 4px; }}

/* ── Exit table ── */
.exit-row {{
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}}
.exit-row:last-child {{ border-bottom: none; }}
.exit-name {{ min-width: 140px; font-weight: 600; font-size: 12px; }}
.exit-bar-wrap {{
  flex: 1;
  height: 20px;
  background: var(--surface2);
  border-radius: 4px;
  overflow: hidden;
  position: relative;
}}
.exit-bar-inner {{
  height: 100%;
  border-radius: 4px;
  display: flex;
  align-items: center;
  padding: 0 8px;
  font-size: 10px;
  font-weight: 700;
  white-space: nowrap;
  min-width: 30px;
}}
.exit-pnl  {{ min-width: 80px; text-align: right; font-weight: 700; font-size: 13px; }}
.exit-count {{ min-width: 55px; text-align: right; color: var(--muted2); font-size: 11px; }}

/* ── Zone grid ── */
.zone-card {{
  border-radius: 8px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  background: var(--surface2);
}}
.zone-name {{
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.08em; margin-bottom: 10px;
}}
.zone-stat {{ display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 12px; }}
.zone-stat-label {{ color: var(--muted2); }}

/* ── Badges ── */
.ticker-badge {{
  display: inline-block; padding: 1px 7px; border-radius: 4px;
  font-size: 10px; font-weight: 700;
}}
.badge-spy  {{ background: rgba(59,130,246,0.2); color: var(--blue); }}
.badge-tqqq {{ background: rgba(249,115,22,0.2); color: var(--orange); }}
.badge-put  {{ background: rgba(59,130,246,0.15); color: var(--blue); font-size: 10px; }}
.badge-call {{ background: rgba(249,115,22,0.15); color: var(--orange); font-size: 10px; }}

/* ── Trades table ── */
.trades-table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
.trades-table th {{
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--muted);
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  position: sticky; top: 0;
  background: var(--surface);
  z-index: 1;
}}
.trades-table td {{ padding: 5px 8px; border-bottom: 1px solid rgba(255,255,255,0.03); }}
.trades-table tr:hover td {{ background: rgba(255,255,255,0.025); }}
.scrollable {{ max-height: 420px; overflow-y: auto; }}
.scrollable::-webkit-scrollbar {{ width: 4px; }}
.scrollable::-webkit-scrollbar-track {{ background: var(--surface2); }}
.scrollable::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}

canvas {{ width: 100% !important; }}
</style>
</head>
<body>

<!-- ═══════════════════════════════ HEADER ═══════════════════════════════ -->
<div class="header">
  <div>
    <div class="header-title">⚡ Theta Harvest — Backtest Dashboard</div>
    <div class="header-sub">{BACKTEST_START} → {BACKTEST_END} &nbsp;·&nbsp; Synthetic Black-Scholes · SPY/TQQQ</div>
    <div class="header-config">Opción B · {config_str}</div>
  </div>
  <div class="gen-time">Generado<br>{generated_at}</div>
</div>

"""

    # ── KPIs per ticker ───────────────────────────────────
    for ticker in tickers:
        m = all_metrics[ticker]
        badge = COLORS.get(ticker, {}).get("badge", "badge-spy")
        if "error" in m:
            html += f'<div style="color:var(--muted);margin-bottom:20px"><span class="ticker-badge {badge}">{ticker}</span> — Sin trades suficientes</div>\n'
            continue

        pnl  = m["total_pnl"]
        wr   = m["win_rate"] * 100
        pf   = m["profit_factor"]
        sh   = m["sharpe"]
        dd   = m["max_drawdown"]
        n    = m["total_trades"]
        # Spreads únicos = total trades / len(TRANCHE_PROFIT_TARGETS) si todos los tranches existen
        n_t = len(TRANCHE_PROFIT_TARGETS)
        n_spreads = n // n_t if n_t > 1 else n

        html += f"""
<div class="section">
  <div class="section-label"><span class="ticker-badge {badge}">{ticker}</span></div>
  <div class="grid-7 mb14">
    <div class="kpi">
      <div class="kpi-label">Total P&amp;L</div>
      <div class="kpi-value {'green' if pnl >= 0 else 'red'}">${pnl:,.0f}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Win Rate</div>
      <div class="kpi-value {'green' if wr >= 80 else 'amber'}">{wr:.1f}%</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Profit Factor</div>
      <div class="kpi-value {'green' if pf >= 3 else 'amber'}">{pf:.2f}×</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Sharpe</div>
      <div class="kpi-value blue">{sh:.1f}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Max Drawdown</div>
      <div class="kpi-value red">${dd:,.0f}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Contratos</div>
      <div class="kpi-value">{n:,}</div>
      <div class="kpi-sub">{n_spreads:,} spreads × {n_t} tranches</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Avg P&amp;L/trade</div>
      <div class="kpi-value {'green' if m['avg_pnl'] >= 0 else 'red'}">${m['avg_pnl']:.2f}</div>
      <div class="kpi-sub">crédito avg {m['avg_credit']*100:.0f}¢</div>
    </div>
  </div>
"""

    # ── Equity curve ─────────────────────────────────────
    html += f"""
  <div class="card mb14">
    <div class="card-title">Equity Curve — P&amp;L Acumulado</div>
    <canvas id="equityChart" style="max-height:260px"></canvas>
  </div>
"""

    # ── Tranche analysis ─────────────────────────────────
    if "by_tranche" in m and m["by_tranche"]:
        bt = m["by_tranche"]
        total_tranche_pnl = sum(d["total_pnl"] for d in bt.values())
        html += """
  <div class="section-label">Tranche Analysis — escalonamiento de salidas</div>
  <div class="grid-3 mb14">
"""
        for tid, d in sorted(bt.items()):
            tgt = d["target_label"]
            pct = (d["total_pnl"] / total_tranche_pnl * 100) if total_tranche_pnl else 0
            pf_val = d["profit_factor"]
            cls = ["amber", "blue", "green"][tid % 3]
            html += f"""
    <div class="tranche-card tranche-{tid}">
      <div class="tranche-badge tb-{tid}">T{tid} &mdash; {tgt}</div>
      <div class="tranche-pnl {cls}">${d['total_pnl']:+,.0f}</div>
      <div style="font-size:11px;color:var(--muted2);margin-top:4px">{pct:.1f}% del total · {d['count']:,} trades</div>
      <div class="tranche-meta">
        <div class="tranche-meta-item">
          <div class="tranche-meta-label">Win Rate</div>
          <div class="tranche-meta-value">{d['win_rate']*100:.1f}%</div>
        </div>
        <div class="tranche-meta-item">
          <div class="tranche-meta-label">Profit Factor</div>
          <div class="tranche-meta-value">{pf_val:.2f}×</div>
        </div>
        <div class="tranche-meta-item">
          <div class="tranche-meta-label">Avg P&amp;L</div>
          <div class="tranche-meta-value {'green' if d['avg_pnl']>=0 else 'red'}">${d['avg_pnl']:+.2f}</div>
        </div>
        <div class="tranche-meta-item">
          <div class="tranche-meta-label">vs T2</div>
          <div class="tranche-meta-value" style="color:var(--muted2);font-size:12px">
            {'base' if tid == max(bt.keys()) else f"-${bt[max(bt.keys())]['total_pnl']-d['total_pnl']:,.0f}"}
          </div>
        </div>
      </div>
    </div>
"""
        html += "  </div>\n"

    # ── DTE table + chart ────────────────────────────────
    byd = m.get("by_dte", {})
    max_pf = max((byd[d]["profit_factor"] for d in byd), default=1)
    html += """
  <div class="section-label">Performance por DTE</div>
  <div class="grid-2 mb14">
    <div class="card">
      <div class="card-title">Tabla por DTE</div>
      <table class="dte-table">
        <thead>
          <tr>
            <th>DTE</th>
            <th>Trades</th>
            <th>Win Rate</th>
            <th>Profit Factor</th>
            <th>Total P&amp;L</th>
            <th>Avg Crédito</th>
          </tr>
        </thead>
        <tbody>
"""
    for dte in all_dtes:
        d_stats = byd.get(dte, {})
        wr_d  = d_stats.get("win_rate", 0) * 100
        pf_d  = d_stats.get("profit_factor", 0)
        pnl_d = d_stats.get("total_pnl", 0)
        cnt_d = d_stats.get("count", 0)
        cred  = d_stats.get("avg_credit", 0)
        bar_w = min(100, pf_d / max_pf * 100) if max_pf > 0 else 0
        bar_color = "#22c55e" if pf_d >= 3 else ("#f59e0b" if pf_d >= 1.5 else "#ef4444")
        dte_label_color = "#22c55e" if pf_d >= 5 else ("#3b82f6" if pf_d >= 2 else "var(--muted2)")
        html += f"""
          <tr>
            <td><div class="dte-badge" style="color:{dte_label_color}">{dte}</div></td>
            <td style="font-weight:600">{cnt_d:,}</td>
            <td style="color:{'var(--green)' if wr_d>=85 else 'var(--amber)'};font-weight:700">{wr_d:.1f}%</td>
            <td>
              <div style="display:flex;align-items:center;gap:8px">
                <span style="font-weight:700;color:{bar_color};min-width:42px">{pf_d:.2f}×</span>
                <div class="pf-bar-wrap" style="flex:1">
                  <div class="pf-bar" style="width:{bar_w:.0f}%;background:{bar_color}"></div>
                </div>
              </div>
            </td>
            <td style="font-weight:700;color:{'var(--green)' if pnl_d>=0 else 'var(--red)'}">${pnl_d:+,.0f}</td>
            <td style="color:var(--muted2)">{cred*100:.0f}¢</td>
          </tr>
"""
    html += """
        </tbody>
      </table>
    </div>
    <div class="card">
      <div class="card-title">P&amp;L por DTE ($ acumulado)</div>
      <canvas id="dteChart" style="max-height:220px"></canvas>
    </div>
  </div>
"""

    # ── Exit reasons ─────────────────────────────────────
    bye = m.get("by_exit", {})
    max_abs_pnl = max((abs(v["total_pnl"]) for v in bye.values()), default=1)
    html += """
  <div class="section-label">Exit Reasons — impacto real en P&amp;L</div>
  <div class="card mb14">
    <div class="card-title">P&amp;L por motivo de salida</div>
"""
    # Sort by absolute P&L
    for reason, stats in sorted(bye.items(), key=lambda x: abs(x[1]["total_pnl"]), reverse=True):
        pnl_e  = stats["total_pnl"]
        cnt_e  = stats["count"]
        bar_w  = abs(pnl_e) / max_abs_pnl * 100
        bar_col = "rgba(34,197,94,0.75)" if pnl_e >= 0 else "rgba(239,68,68,0.75)"
        pnl_cls = "green" if pnl_e >= 0 else "red"
        icon = "✅" if pnl_e > 0 else "🛑"
        html += f"""
    <div class="exit-row">
      <div class="exit-name">{icon} {reason}</div>
      <div class="exit-bar-wrap">
        <div class="exit-bar-inner" style="width:{bar_w:.1f}%;background:{bar_col}">
          {cnt_e} trades
        </div>
      </div>
      <div class="exit-pnl {pnl_cls}">${pnl_e:+,.0f}</div>
      <div class="exit-count">{cnt_e:,} trades</div>
    </div>
"""
    html += "  </div>\n"

    # ── Risk zones + PUT vs CALL ──────────────────────────
    byz    = m.get("by_zone", {})
    by_type = m.get("by_type", {})
    html += """
  <div class="section-label">Risk Zones &amp; Dirección</div>
  <div class="grid-2 mb14">
    <div class="card">
      <div class="card-title">Risk Zones (distancia a soporte/resistencia)</div>
      <div class="grid-3" style="gap:10px">
"""
    zone_colors = {
        "VERY_LOW": ("var(--green)",  "rgba(34,197,94,0.08)"),
        "LOW":      ("var(--blue)",   "rgba(59,130,246,0.08)"),
        "MID":      ("var(--amber)",  "rgba(245,158,11,0.08)"),
    }
    for zone in ["VERY_LOW", "LOW", "MID"]:
        zs = byz.get(zone, {})
        col, bg = zone_colors.get(zone, ("var(--muted2)", "var(--surface2)"))
        wr_z = zs.get("win_rate", 0) * 100
        pf_z = zs.get("profit_factor", 0)
        pnl_z = zs.get("total_pnl", 0)
        cnt_z = zs.get("count", 0)
        zone_label = zone.replace("_", " ")
        html += f"""
        <div class="zone-card" style="border-color:rgba(255,255,255,0.07);background:{bg}">
          <div class="zone-name" style="color:{col}">{zone_label}</div>
          <div class="zone-stat"><span class="zone-stat-label">Trades</span><strong>{cnt_z:,}</strong></div>
          <div class="zone-stat"><span class="zone-stat-label">Win Rate</span><strong style="color:{col}">{wr_z:.1f}%</strong></div>
          <div class="zone-stat"><span class="zone-stat-label">PF</span><strong>{pf_z:.2f}×</strong></div>
          <div class="zone-stat"><span class="zone-stat-label">P&amp;L</span><strong style="color:{'var(--green)' if pnl_z>=0 else 'var(--red)'}">${pnl_z:+,.0f}</strong></div>
        </div>
"""
    html += """
      </div>
    </div>
    <div class="card">
      <div class="card-title">PUT vs CALL spreads</div>
      <canvas id="typeChart" style="max-height:220px"></canvas>
    </div>
  </div>
"""

    # ── Trades table ─────────────────────────────────────
    pts = m.get("cumulative_pnl", [])[-300:]
    pts_sorted = sorted(pts, key=lambda p: p["date"], reverse=True)
    html += """
  <div class="section-label">Trades recientes</div>
  <div class="card">
    <div class="card-title">Últimos 300 trades (por fecha de cierre)</div>
    <div class="scrollable">
    <table class="trades-table">
      <thead>
        <tr>
          <th>Fecha</th>
          <th>Tranche</th>
          <th>DTE</th>
          <th>Tipo</th>
          <th>Zona</th>
          <th>Crédito</th>
          <th>Exit</th>
          <th>P&amp;L</th>
          <th>Acum.</th>
        </tr>
      </thead>
      <tbody>
"""
    for p in pts_sorted:
        pnl_cls = "green" if p["pnl"] >= 0 else "red"
        cum_cls = "green" if p["cum"] >= 0 else "red"
        typ = "PUT" if "put" in p["type"] else "CALL"
        typ_cls = "badge-put" if typ == "PUT" else "badge-call"
        tid = p.get("tranche_id", "?")
        ttgt = p.get("tranche_target")
        t_label = f"T{tid}={'EXPIRY' if ttgt is None else str(int(ttgt*100))+'%'}"
        t_color = ["var(--amber)", "var(--blue)", "var(--green)"][tid if isinstance(tid, int) and tid < 3 else 0]
        html += f"""        <tr>
          <td>{p['date'][:10]}</td>
          <td style="font-weight:700;color:{t_color};font-size:10px">{t_label}</td>
          <td style="text-align:center;font-weight:700">{p['dte']}</td>
          <td><span class="ticker-badge {typ_cls}">{typ}</span></td>
          <td style="color:var(--muted2)">{p['zone']}</td>
          <td style="color:var(--muted2)">{p['credit']:.2f}</td>
          <td style="color:var(--muted);font-size:10px">{p['exit']}</td>
          <td style="font-weight:700" class="{pnl_cls}">${p['pnl']:+.2f}</td>
          <td style="font-weight:600" class="{cum_cls}">${p['cum']:+,.0f}</td>
        </tr>
"""
    html += """      </tbody>
    </table>
    </div>
  </div>
</div>
"""

    # ── JavaScript ───────────────────────────────────────
    # Collect chart data from first ticker with data
    ticker_chart = tickers_with_data[0] if tickers_with_data else None
    if ticker_chart:
        m_c = all_metrics[ticker_chart]
        byd_c = m_c.get("by_dte", {})
        by_type_c = m_c.get("by_type", {})
        c = COLORS.get(ticker_chart, {"border": "#3b82f6", "bg": "rgba(59,130,246,0.15)"})

        dte_labels = [f"DTE {d}" for d in all_dtes]
        dte_pnl    = [byd_c.get(d, {}).get("total_pnl", 0) for d in all_dtes]
        dte_bgcol  = [
            "rgba(34,197,94,0.7)" if byd_c.get(d, {}).get("profit_factor", 0) >= 5
            else ("rgba(59,130,246,0.7)" if byd_c.get(d, {}).get("profit_factor", 0) >= 2
                  else "rgba(245,158,11,0.7)")
            for d in all_dtes
        ]

        type_labels = ["PUT Spread", "CALL Spread"]
        type_pnl    = [
            by_type_c.get("put_credit_spread", {}).get("total_pnl", 0),
            by_type_c.get("call_credit_spread", {}).get("total_pnl", 0),
        ]
        type_wr = [
            by_type_c.get("put_credit_spread", {}).get("win_rate", 0) * 100,
            by_type_c.get("call_credit_spread", {}).get("win_rate", 0) * 100,
        ]

    equity_ds_json = _safe_json(equity_datasets)

    html += f"""
<script>
Chart.defaults.color = '#64748b';
Chart.defaults.borderColor = '#252940';

// ── Equity chart ─────────────────────────────────────────
const eqCtx = document.getElementById('equityChart');
if (eqCtx) {{
  new Chart(eqCtx, {{
    type: 'line',
    data: {{ datasets: {equity_ds_json} }},
    options: {{
      responsive: true,
      interaction: {{ intersect: false, mode: 'index' }},
      scales: {{
        x: {{
          type: 'time',
          time: {{ unit: 'month', displayFormats: {{ month: 'MMM yy' }} }},
          grid: {{ color: '#1c2030' }},
          ticks: {{ font: {{ size: 11 }} }},
        }},
        y: {{
          grid: {{ color: '#1c2030' }},
          ticks: {{
            font: {{ size: 11 }},
            callback: v => '$' + v.toLocaleString(),
          }},
        }},
      }},
      plugins: {{
        legend: {{ position: 'top', labels: {{ font: {{ size: 12 }}, boxWidth: 14 }} }},
        tooltip: {{
          callbacks: {{
            label: ctx => ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString(undefined, {{minimumFractionDigits: 0, maximumFractionDigits: 0}}),
          }},
        }},
      }},
    }},
  }});
}}
"""

    if ticker_chart:
        html += f"""
// ── DTE chart ────────────────────────────────────────────
const dteCtx = document.getElementById('dteChart');
if (dteCtx) {{
  new Chart(dteCtx, {{
    type: 'bar',
    data: {{
      labels: {_safe_json(dte_labels)},
      datasets: [{{
        label: 'P&L Total',
        data: {_safe_json(dte_pnl)},
        backgroundColor: {_safe_json(dte_bgcol)},
        borderRadius: 6,
      }}],
    }},
    options: {{
      responsive: true,
      scales: {{
        x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 12, weight: '700' }} }} }},
        y: {{
          grid: {{ color: '#1c2030' }},
          ticks: {{ callback: v => '$' + v.toLocaleString(), font: {{ size: 11 }} }},
        }},
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: ctx => 'P&L: $' + ctx.raw.toLocaleString(),
          }},
        }},
      }},
    }},
  }});
}}

// ── PUT vs CALL chart ────────────────────────────────────
const typeCtx = document.getElementById('typeChart');
if (typeCtx) {{
  new Chart(typeCtx, {{
    type: 'bar',
    data: {{
      labels: {_safe_json(type_labels)},
      datasets: [
        {{
          label: 'P&L Total',
          data: {_safe_json(type_pnl)},
          backgroundColor: ['rgba(59,130,246,0.7)', 'rgba(249,115,22,0.7)'],
          borderRadius: 6,
          yAxisID: 'y',
        }},
        {{
          label: 'Win Rate %',
          data: {_safe_json(type_wr)},
          backgroundColor: ['rgba(59,130,246,0.2)', 'rgba(249,115,22,0.2)'],
          borderColor: ['rgba(59,130,246,0.8)', 'rgba(249,115,22,0.8)'],
          borderWidth: 2,
          borderRadius: 6,
          yAxisID: 'y2',
          type: 'line',
        }},
      ],
    }},
    options: {{
      responsive: true,
      scales: {{
        x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 12, weight: '700' }} }} }},
        y: {{
          grid: {{ color: '#1c2030' }},
          ticks: {{ callback: v => '$' + v.toLocaleString(), font: {{ size: 11 }} }},
          position: 'left',
        }},
        y2: {{
          grid: {{ display: false }},
          ticks: {{ callback: v => v.toFixed(0) + '%', font: {{ size: 11 }} }},
          position: 'right',
          min: 0, max: 105,
        }},
      }},
      plugins: {{
        legend: {{ position: 'top', labels: {{ font: {{ size: 11 }}, boxWidth: 12 }} }},
      }},
    }},
  }});
}}
"""

    html += """
</script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path
