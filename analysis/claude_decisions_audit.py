"""
analysis/claude_decisions_audit.py

Pre-cleanup audit de las 3 collections de Claude decisions antes del drop F3.
Cuantifica el track record de Claude para informar decisión post-F4 sobre
safety net status.

Approach:
- Match decisions ↔ trades via strategy field (claude_*) del trade record
- P&L usa pnl_usd directo del trade (no FIFO matching)
- Lectura desde Firestore live

Output:
- Console: summary table
- analysis/claude_decisions_audit.md: reporte completo

Uso:
    python3 analysis/claude_decisions_audit.py
"""

from datetime import datetime, timezone
from collections import defaultdict, Counter
from google.cloud import firestore


PROJECT = 'eolo-schwab-agent'
OUTPUT_MD = 'analysis/claude_decisions_audit.md'

THRESHOLD_MIN_TRADES = 20
THRESHOLD_WIN_RATE = 50
THRESHOLD_PNL_POSITIVE = 0


def section_header(title, width=70):
    return f"\n{'='*width}\n{title}\n{'='*width}\n"


# ============================================================
# SECCIÓN 1: Counts agregados
# ============================================================
def analyze_counts(db):
    print("Analyzing counts agregados...")
    result = {}

    coll1 = db.collection('eolo-claude-bot-decisions-v2')
    containers_1 = list(coll1.list_documents())
    sub_count_1 = 0
    for cont in containers_1:
        subs = list(cont.collection('decisions').list_documents())
        sub_count_1 += len(subs)
    result['eolo-claude-bot-decisions-v2'] = {
        'containers': len(containers_1),
        'sub_docs': sub_count_1,
        'pattern': 'containers + subcollection',
    }

    coll2 = db.collection('eolo-claude-decisions-v2')
    containers_2 = list(coll2.list_documents())
    sub_count_2 = 0
    for cont in containers_2:
        subs = list(cont.collection('decisions').list_documents())
        sub_count_2 += len(subs)
    result['eolo-claude-decisions-v2'] = {
        'containers': len(containers_2),
        'sub_docs': sub_count_2,
        'pattern': 'containers + subcollection',
    }

    coll3 = db.collection('eolo-crypto-claude-decisions')
    flat_docs = list(coll3.list_documents())
    result['eolo-crypto-claude-decisions'] = {
        'containers': 0,
        'sub_docs': len(flat_docs),
        'pattern': 'flat',
    }

    return result


# ============================================================
# SECCIÓN 2: V2 Mispricing
# ============================================================
def analyze_v2_mispricing(db):
    print("Analyzing V2 mispricing decisions...")
    coll = db.collection('eolo-claude-decisions-v2')

    by_mispricing_type = Counter()
    by_action = Counter()
    by_confidence = Counter()
    by_ticker = Counter()
    by_option_type = Counter()
    total = 0

    for container in coll.list_documents():
        for doc in container.collection('decisions').stream():
            d = doc.to_dict() or {}
            by_mispricing_type[d.get('mispricing_type', 'UNKNOWN')] += 1
            by_action[d.get('action', 'UNKNOWN')] += 1
            by_confidence[d.get('confidence', 'UNKNOWN')] += 1
            by_ticker[d.get('ticker', 'UNKNOWN')] += 1
            by_option_type[d.get('option_type', 'UNKNOWN')] += 1
            total += 1

    return {
        'total': total,
        'by_mispricing_type': dict(by_mispricing_type.most_common()),
        'by_action': dict(by_action.most_common()),
        'by_confidence': dict(by_confidence.most_common()),
        'by_ticker': dict(by_ticker.most_common(15)),
        'by_option_type': dict(by_option_type.most_common()),
    }


# ============================================================
# SECCIÓN 3: V2 General Bot
# ============================================================
def analyze_v2_general(db):
    print("Analyzing V2 general bot decisions...")
    coll = db.collection('eolo-claude-bot-decisions-v2')

    by_signal = Counter()
    by_strategy_used = Counter()
    by_ticker = Counter()
    confidences = []
    errors = 0
    total = 0

    for container in coll.list_documents():
        for doc in container.collection('decisions').stream():
            d = doc.to_dict() or {}
            by_signal[d.get('signal', 'UNKNOWN')] += 1
            by_strategy_used[d.get('strategy_used', 'UNKNOWN')] += 1
            by_ticker[d.get('ticker', 'UNKNOWN')] += 1

            confidence = d.get('confidence')
            if isinstance(confidence, (int, float)):
                confidences.append(confidence)

            if d.get('error'):
                errors += 1
            total += 1

    avg_conf = sum(confidences) / len(confidences) if confidences else None

    return {
        'total': total,
        'errors': errors,
        'error_rate_pct': round(errors / total * 100, 1) if total else 0,
        'by_signal': dict(by_signal.most_common()),
        'by_strategy_used': dict(by_strategy_used.most_common(10)),
        'by_ticker': dict(by_ticker.most_common(15)),
        'confidence_stats': {
            'count': len(confidences),
            'avg': round(avg_conf, 3) if avg_conf else None,
            'min': min(confidences) if confidences else None,
            'max': max(confidences) if confidences else None,
        },
    }


# ============================================================
# SECCIÓN 4: Crypto
# ============================================================
def analyze_crypto(db):
    print("Analyzing Crypto Claude decisions...")
    coll = db.collection('eolo-crypto-claude-decisions')

    by_action = Counter()
    by_symbol = Counter()
    by_confidence = Counter()
    by_mode = Counter()
    total_cost = 0.0
    total = 0

    for doc in coll.stream():
        d = doc.to_dict() or {}
        by_action[d.get('action', 'UNKNOWN')] += 1
        by_symbol[d.get('symbol', 'UNKNOWN')] += 1
        by_confidence[d.get('confidence', 'UNKNOWN')] += 1
        by_mode[d.get('mode', 'UNKNOWN')] += 1

        cost = d.get('approx_cost_usd', 0)
        if isinstance(cost, (int, float)):
            total_cost += cost
        total += 1

    return {
        'total': total,
        'by_action': dict(by_action.most_common()),
        'by_symbol': dict(by_symbol.most_common(15)),
        'by_confidence': dict(by_confidence.most_common()),
        'by_mode': dict(by_mode.most_common()),
        'total_cost_usd': round(total_cost, 2),
    }


# ============================================================
# SECCIÓN 5: Date Ranges
# ============================================================
def analyze_date_ranges(db):
    print("Analyzing date ranges...")
    result = {}

    coll1 = db.collection('eolo-claude-bot-decisions-v2')
    containers_1 = sorted([c.id for c in coll1.list_documents()])
    result['eolo-claude-bot-decisions-v2'] = {
        'oldest': containers_1[0] if containers_1 else None,
        'newest': containers_1[-1] if containers_1 else None,
        'days': len(containers_1),
    }

    coll2 = db.collection('eolo-claude-decisions-v2')
    containers_2 = sorted([c.id for c in coll2.list_documents()])
    result['eolo-claude-decisions-v2'] = {
        'oldest': containers_2[0] if containers_2 else None,
        'newest': containers_2[-1] if containers_2 else None,
        'days': len(containers_2),
    }

    coll3 = db.collection('eolo-crypto-claude-decisions')
    dates_crypto = set()
    for doc in coll3.stream():
        date_part = doc.id.split('-')[:3]
        if len(date_part) == 3:
            dates_crypto.add('-'.join(date_part))
    dates_sorted = sorted(dates_crypto)
    result['eolo-crypto-claude-decisions'] = {
        'oldest': dates_sorted[0] if dates_sorted else None,
        'newest': dates_sorted[-1] if dates_sorted else None,
        'days': len(dates_sorted),
    }

    return result


# ============================================================
# SECCIÓN 6: Match decisions ↔ trades + P&L (CRÍTICO)
# ============================================================
def match_decisions_to_trades(db):
    print("Matching decisions with trades (CRITICAL)...")
    result = {'v2': {}, 'crypto': {}}

    print("  Loading V2 trades...")
    by_strategy_v2 = defaultdict(lambda: {
        'count': 0, 'BUY_TO_OPEN': 0, 'SELL_TO_CLOSE': 0,
        'total_pnl': 0.0, 'pnl_pos': 0, 'pnl_neg': 0, 'pnl_zero': 0,
        'pnl_values': [],
    })

    for daily_doc in db.collection('eolo-options-trades').stream():
        day_data = daily_doc.to_dict() or {}
        # Cada daily doc contiene los trades como FIELDS ({ts}_{ticker}_{action} → trade payload)
        for field_key, t in day_data.items():
            if not isinstance(t, dict):
                continue
            strategy = t.get('strategy', '')
            if not strategy.startswith('claude_'):
                continue

            action = t.get('action', 'UNKNOWN')
            pnl = t.get('pnl_usd')

            by_strategy_v2[strategy]['count'] += 1
            if action == 'BUY_TO_OPEN':
                by_strategy_v2[strategy]['BUY_TO_OPEN'] += 1
            elif action == 'SELL_TO_CLOSE':
                by_strategy_v2[strategy]['SELL_TO_CLOSE'] += 1

            if isinstance(pnl, (int, float)):
                by_strategy_v2[strategy]['total_pnl'] += pnl
                by_strategy_v2[strategy]['pnl_values'].append(pnl)
                if pnl > 0:
                    by_strategy_v2[strategy]['pnl_pos'] += 1
                elif pnl < 0:
                    by_strategy_v2[strategy]['pnl_neg'] += 1
                else:
                    by_strategy_v2[strategy]['pnl_zero'] += 1

    for strategy, stats in by_strategy_v2.items():
        wins = stats['pnl_pos']
        losses = stats['pnl_neg']
        decided = wins + losses
        stats['win_rate_pct'] = round(wins / decided * 100, 1) if decided > 0 else None
        n = len(stats['pnl_values'])
        stats['avg_pnl'] = round(stats['total_pnl'] / n, 2) if n > 0 else None
        del stats['pnl_values']

    result['v2'] = dict(by_strategy_v2)

    print("  Loading Crypto trades (this may take a moment, ~36k docs)...")
    by_strategy_crypto = defaultdict(lambda: {
        'count': 0, 'BUY': 0, 'SELL': 0,
        'total_pnl': 0.0, 'pnl_pos': 0, 'pnl_neg': 0, 'pnl_zero': 0,
        'pnl_values': [],
    })

    for trade_doc in db.collection('eolo-crypto-trades').stream():
        t = trade_doc.to_dict() or {}
        strategy = t.get('strategy', '')
        if not strategy.startswith('claude_'):
            continue

        action = t.get('action', 'UNKNOWN').upper()
        pnl = t.get('pnl_usdt')   # crypto persiste pnl como pnl_usdt

        by_strategy_crypto[strategy]['count'] += 1
        if 'BUY' in action:
            by_strategy_crypto[strategy]['BUY'] += 1
        elif 'SELL' in action:
            by_strategy_crypto[strategy]['SELL'] += 1

        if isinstance(pnl, (int, float)):
            by_strategy_crypto[strategy]['total_pnl'] += pnl
            by_strategy_crypto[strategy]['pnl_values'].append(pnl)
            if pnl > 0:
                by_strategy_crypto[strategy]['pnl_pos'] += 1
            elif pnl < 0:
                by_strategy_crypto[strategy]['pnl_neg'] += 1
            else:
                by_strategy_crypto[strategy]['pnl_zero'] += 1

    for strategy, stats in by_strategy_crypto.items():
        wins = stats['pnl_pos']
        losses = stats['pnl_neg']
        decided = wins + losses
        stats['win_rate_pct'] = round(wins / decided * 100, 1) if decided > 0 else None
        n = len(stats['pnl_values'])
        stats['avg_pnl'] = round(stats['total_pnl'] / n, 2) if n > 0 else None
        del stats['pnl_values']

    result['crypto'] = dict(by_strategy_crypto)

    return result


# ============================================================
# Recomendación cuantitativa
# ============================================================
def generate_recommendation(s6):
    recs = []
    for label, source in [('V2', s6['v2']), ('Crypto', s6['crypto'])]:
        for strategy, stats in source.items():
            count = stats.get('count', 0)
            pnl = stats.get('total_pnl', 0)
            wr = stats.get('win_rate_pct')

            if count < THRESHOLD_MIN_TRADES:
                recs.append(f"⚠️ {label} {strategy}: solo {count} trades — sample size insuficiente (observar más post-F4)")
            elif pnl > THRESHOLD_PNL_POSITIVE and wr and wr > THRESHOLD_WIN_RATE:
                recs.append(f"✅ {label} {strategy}: P&L ${pnl:+.2f}, WR {wr}% — CANDIDATO a considerar como safety net post-observación")
            elif pnl < 0 or (wr is not None and wr < 40):
                recs.append(f"❌ {label} {strategy}: P&L ${pnl:+.2f}, WR {wr}% — CONFIRMAR como strategy normal (no safety net)")
            else:
                recs.append(f"🟡 {label} {strategy}: P&L ${pnl:+.2f}, WR {wr}% — zona gris, observación post-F4 dirá")
    return recs


# ============================================================
# Write Markdown Report
# ============================================================
def write_markdown_report(s1, s2, s3, s4, s5, s6, recs, output_path):
    lines = []
    lines.append("# Claude Decisions Audit — Pre F3 Cleanup")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append("Audit cuantitativo del track record de Claude decisions antes del cleanup F3.")
    lines.append("Objetivo: informar decisión post-F4 sobre si Claude debe ser tratado como safety net.\n")

    lines.append("## Resumen ejecutivo\n")
    total_decisions = sum(info['sub_docs'] for info in s1.values())
    lines.append(f"- **Total decisions auditadas:** {total_decisions}")

    v2_total_pnl = sum(stats['total_pnl'] for stats in s6['v2'].values())
    v2_total_trades = sum(stats['count'] for stats in s6['v2'].values())
    lines.append(f"- **V2 Claude trades:** {v2_total_trades}, P&L ${v2_total_pnl:+.2f}")

    crypto_total_pnl = sum(stats['total_pnl'] for stats in s6['crypto'].values())
    crypto_total_trades = sum(stats['count'] for stats in s6['crypto'].values())
    lines.append(f"- **Crypto Claude trades:** {crypto_total_trades}, P&L ${crypto_total_pnl:+.2f}")
    lines.append(f"- **Crypto API cost (estimated):** ${s4['total_cost_usd']}\n")

    lines.append("## 1. Counts agregados\n")
    lines.append("| Collection | Containers | Sub-docs | Pattern |")
    lines.append("|---|---|---|---|")
    for coll, info in s1.items():
        lines.append(f"| `{coll}` | {info['containers']} | {info['sub_docs']} | {info['pattern']} |")

    lines.append(f"\n## 2. V2 Mispricing Decisions (`eolo-claude-decisions-v2`)\n")
    lines.append(f"Total: **{s2['total']}** decisions\n")
    lines.append("### By Confidence (genera strategy=claude_high/medium en trades)")
    for conf, count in s2['by_confidence'].items():
        lines.append(f"- `{conf}`: {count}")
    lines.append("\n### By Action")
    for action, count in s2['by_action'].items():
        lines.append(f"- `{action}`: {count}")
    lines.append("\n### By Mispricing Type")
    for mt, count in s2['by_mispricing_type'].items():
        lines.append(f"- `{mt}`: {count}")
    lines.append("\n### Top 15 Tickers")
    for ticker, count in s2['by_ticker'].items():
        lines.append(f"- `{ticker}`: {count}")

    lines.append(f"\n## 3. V2 General Bot Decisions (`eolo-claude-bot-decisions-v2`)\n")
    lines.append(f"Total: **{s3['total']}** decisions, **{s3['errors']}** errors ({s3['error_rate_pct']}%)\n")
    lines.append("### By Signal")
    for sig, count in s3['by_signal'].items():
        lines.append(f"- `{sig}`: {count}")
    cs = s3['confidence_stats']
    if cs['count']:
        lines.append(f"\n### Confidence Stats (numeric)")
        lines.append(f"- Count: {cs['count']}, Avg: {cs['avg']}, Range: [{cs['min']}, {cs['max']}]")
    lines.append("\n### Top 10 Strategy Used")
    for s, count in s3['by_strategy_used'].items():
        lines.append(f"- `{s}`: {count}")

    lines.append(f"\n## 4. Crypto Claude Decisions (`eolo-crypto-claude-decisions`)\n")
    lines.append(f"Total: **{s4['total']}** decisions")
    lines.append(f"Total API cost (estimated): **${s4['total_cost_usd']}**\n")
    lines.append("### By Action")
    for action, count in s4['by_action'].items():
        lines.append(f"- `{action}`: {count}")
    lines.append("\n### By Confidence")
    for conf, count in s4['by_confidence'].items():
        lines.append(f"- `{conf}`: {count}")
    lines.append("\n### Top 15 Symbols")
    for sym, count in s4['by_symbol'].items():
        lines.append(f"- `{sym}`: {count}")
    lines.append("\n### By Mode")
    for mode, count in s4['by_mode'].items():
        lines.append(f"- `{mode}`: {count}")

    lines.append("\n## 5. Date Ranges\n")
    lines.append("| Collection | Oldest | Newest | Days |")
    lines.append("|---|---|---|---|")
    for coll, info in s5.items():
        lines.append(f"| `{coll}` | {info['oldest']} | {info['newest']} | {info['days']} |")

    lines.append("\n## 6. Decisions → Trades Match + P&L (CRITICAL)\n")
    lines.append("### V2 (eolo-options-trades, strategy starts with `claude_`)\n")
    lines.append("| Strategy | Trades | BUY_TO_OPEN | SELL_TO_CLOSE | P&L Total | Avg P&L | Wins | Losses | Win Rate |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for strategy, stats in sorted(s6['v2'].items()):
        wr = f"{stats['win_rate_pct']}%" if stats['win_rate_pct'] is not None else "—"
        avg = f"${stats['avg_pnl']:+.2f}" if stats['avg_pnl'] is not None else "—"
        lines.append(
            f"| `{strategy}` | {stats['count']} | {stats['BUY_TO_OPEN']} | {stats['SELL_TO_CLOSE']} | "
            f"${stats['total_pnl']:+.2f} | {avg} | {stats['pnl_pos']} | {stats['pnl_neg']} | {wr} |"
        )

    lines.append("\n### Crypto (eolo-crypto-trades, strategy starts with `claude_`)\n")
    lines.append("| Strategy | Trades | BUY | SELL | P&L Total | Avg P&L | Wins | Losses | Win Rate |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for strategy, stats in sorted(s6['crypto'].items()):
        wr = f"{stats['win_rate_pct']}%" if stats['win_rate_pct'] is not None else "—"
        avg = f"${stats['avg_pnl']:+.2f}" if stats['avg_pnl'] is not None else "—"
        lines.append(
            f"| `{strategy}` | {stats['count']} | {stats['BUY']} | {stats['SELL']} | "
            f"${stats['total_pnl']:+.2f} | {avg} | {stats['pnl_pos']} | {stats['pnl_neg']} | {wr} |"
        )

    lines.append("\n## 7. Recomendaciones cuantitativas\n")
    for rec in recs:
        lines.append(f"- {rec}")

    lines.append("\n### Criterios de evaluación")
    lines.append(f"- ✅ CANDIDATO safety net: P&L > $0, win rate > {THRESHOLD_WIN_RATE}%, ≥ {THRESHOLD_MIN_TRADES} trades")
    lines.append("- ❌ NO safety net: P&L < $0 o win rate < 40%")
    lines.append("- 🟡 Zona gris: intermedio")
    lines.append(f"- ⚠️ Insuficiente data: < {THRESHOLD_MIN_TRADES} trades")

    lines.append("\n## 8. Próximos pasos post-F4\n")
    lines.append("1. Observar 1 semana con Pure Isolation estricto (claude como strategy normal)")
    lines.append("2. Re-correr este audit con data post-Pure-Isolation")
    lines.append("3. Comparar P&L y win rate con baseline pre-cleanup (este reporte)")
    lines.append("4. Si claude_* variants mantienen track record positivo: considerar agregar a SAFETY_NETS")
    lines.append("5. Si confirman track record negativo o cross-attribution era artificial: confirmar diseño actual")

    content = "\n".join(lines)
    with open(output_path, 'w') as f:
        f.write(content)
    print(f"\n✓ Report written to {output_path}")


# ============================================================
# Main
# ============================================================
def main():
    print(section_header("CLAUDE DECISIONS AUDIT — PRE F3 CLEANUP"))

    db = firestore.Client(project=PROJECT)

    s1 = analyze_counts(db)
    s2 = analyze_v2_mispricing(db)
    s3 = analyze_v2_general(db)
    s4 = analyze_crypto(db)
    s5 = analyze_date_ranges(db)
    s6 = match_decisions_to_trades(db)
    recs = generate_recommendation(s6)

    print(section_header("CONSOLE SUMMARY"))

    print("\n--- Counts ---")
    for coll, info in s1.items():
        print(f"  {coll}: {info['containers']} containers, {info['sub_docs']} sub-docs")

    print("\n--- V2 Claude Trades (key result) ---")
    for strategy, stats in sorted(s6['v2'].items()):
        wr = f"{stats['win_rate_pct']}%" if stats['win_rate_pct'] is not None else "—"
        print(f"  {strategy}: {stats['count']} trades, P&L ${stats['total_pnl']:+.2f}, WR {wr}")

    print("\n--- Crypto Claude Trades (key result) ---")
    for strategy, stats in sorted(s6['crypto'].items()):
        wr = f"{stats['win_rate_pct']}%" if stats['win_rate_pct'] is not None else "—"
        print(f"  {strategy}: {stats['count']} trades, P&L ${stats['total_pnl']:+.2f}, WR {wr}")

    print("\n--- Recomendaciones ---")
    for rec in recs:
        print(f"  {rec}")

    write_markdown_report(s1, s2, s3, s4, s5, s6, recs, OUTPUT_MD)

    print(f"\nDone. Full report: {OUTPUT_MD}")


if __name__ == '__main__':
    main()
