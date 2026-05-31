# Stack Audit — S3 + Front + Backend Status

**Fecha:** 2026-05-31 (Domingo cierre)
**Pregunta:** ¿Qué pasó con S3? ¿Dónde estamos parados en front + backend?
**Resultado:** S3 COMPLETO al 100%. Stack maduro pero con gaps específicos identificados.

---

## PARTE 1 — Sprint S3 status

### Cronología completa (12 sub-sprints)

| Sprint | Fecha | Status | Qué hizo |
|---|---|---|---|
| **S3** (in-memory base) | 2026-05-13 | ✅ LIVE | POST /api/state/edit + allowlist + override layer + Flask + 3 readonly overlaps |
| **S3.5** (position sizing) | 2026-05-14 | ✅ LIVE | Granular qty matrix 5x4x5 = 100 valores editables |
| **S3.1-A** | 2026-05-20 | ✅ LIVE | Exits Advanced thresholds → instance vars funcionales |
| **S3.1-B** | 2026-05-20 | ✅ LIVE | Refactor `_strategy_params()` + scan call con thresholds editados |
| **S3.1-C** | 2026-05-20 | ✅ LIVE | VIX velocity (window + thresholds) editables via UI |
| **S3.2** | 2026-05-20 | ✅ LIVE | `delta_by_risk` dict editable, propagado a scan |
| **S3.3** | 2026-05-20 | ✅ LIVE | `TICKER_CONFIG` per-ticker editable (deep copy mutable) |
| **S3.4** | 2026-05-20 | ✅ LIVE | `VIX_CREDIT_TABLE` list-of-lists editable |
| **S3.X** (Firestore persistence) | 2026-05-29 | ✅ LIVE | Sprint 15 Fase A+B+C+D+E: persiste overrides al boot, sobrevive restart |
| **S3 Audit (Fase A)** | 2026-05-29 | ✅ LIVE | Audit estado persistencia Firestore |
| **S3 Fase E** | 2026-05-29 | ✅ LIVE | Fix timezone consistency dashboard |
| **S3 Ventana trading** | 2026-05-29 | ✅ LIVE | HH:MM regex configurable (entry_window editable) |

### Resumen S3

**TODO LIVE.** 12 sub-sprints, ~14 días de trabajo. S3 es uno de los stacks más maduros del proyecto.

Override layer funciona end-to-end:
- UI envía POST `/api/state/edit` con paths bracket-notation
- Backend valida (3 capas: allowlist + range + cross-field)
- Override se guarda en `bot._strategy_overrides` dict
- `_apply_strategy_overrides_to_instance_vars()` propaga a vars usadas en scan
- Firestore persistence: `eolo-crop-config/strategy_overrides` doc, restaura al boot
- Restart preserva overrides
- Dashboard refleja valores en tiempo real

### Tech debt residual de S3

**Ninguna conocida.** El backlog 29-may NO lista issues activos del stack S3. Si emergen, irán a nueva categoría OP-X.

---

## PARTE 2 — Frontend status

### Secciones existentes en `eolo-crop/dashboard-crop.html` (5397 LOC)

| # | Sección | Propósito | Status |
|---|---|---|---|
| 1 | 🚨 Cerrar todas las posiciones | Botón emergencia POST /api/close-all | ✅ Funcional |
| 2 | Cards (P&L, Crédito, Win Rate, Spreads) | KPIs principales | ✅ Funcional |
| 3 | 🌾 THETA HARVEST | 4 charts ticker price/PnL toggle | ✅ Funcional (Sprint 15 timezone) |
| 4 | Σ Greeks Agregados | Portfolio greeks | ✅ Funcional |
| 5 | 🎯 Tickers a Operar | Toggle activación per-ticker | ✅ Funcional |
| 6 | 📈 Curva P&L Intradía | Round-trips cronológico | ✅ Funcional |
| 7 | 🏆 Performance | Donut win rate + heatmap + ranking + DTE breakdown | ✅ Funcional |
| 8 | 📝 Paper Trades | Historial completo del día | ✅ Funcional |
| 9 | **🧠 LLM Metrics** | KPIs + 3 charts + 2 tables LLM observability | ⏳ **PR #27 draft** (deploy lunes) |
| 10 | 🛡 Risk Management | Daily loss cap, position slots, strategy breakdown, capital | ✅ Funcional |
| 11 | 📊 Parámetros Estrategia | 7 sub-paneles editables (B3-B7 + Position Sizing) | ✅ Funcional (Sprint S3 stack) |

### Gaps frontend identificados

#### Alta prioridad — info que falta visualizar

| Gap | Por qué importa | Esfuerzo estimado |
|---|---|---|
| **A. Trade detail expandido** | Click en trade del historial → expandir con LLM reasoning, tacit_rules, decision_meta. Hoy historial es flat | 3-4h |
| **B. LLM decision history** | Última N decisiones del LLM con verdict/confidence/reason. Hoy solo se ven KPIs agregados en UP-2.2 | 2-3h |
| **C. KB inspection inline** | Qué reglas se citaron en última decisión LLM (decision.tacit_rules_applied). Hoy requiere curl + parse | 2-3h |
| **D. Alerts panel** | Notificaciones de eventos: stop loss disparado, EOD close, VIX spike, errors. Hoy solo en logs | 3-4h |
| **E. Cost trends chart** | UP-2.2 muestra cost actual pero no histórico. Curva intraday de cost_estimate_usd | 1-2h |

#### Media prioridad — info nice-to-have

| Gap | Por qué importa | Esfuerzo |
|---|---|---|
| F. Comparación rule-based vs LLM | QQQ/IWM/TQQQ (rule-based) vs SPY (LLM) performance side-by-side | 3-4h |
| G. Sector dashboard | Sprint 12 trackea SectorDir per-ticker. Hoy invisible al usuario | 2-3h |
| H. Strategy override audit log | Historial de cambios via /api/state/edit (quién/cuándo/qué). Hoy solo en logs | 4-5h |
| I. Backtest viewer | Cuando UP-3.2 esté listo | 4-5h post UP-3.2 |
| J. KB Editor inline | Cuando UP-1.2 fase 2 esté listo | 4-6h post UP-1.2 |

#### Baja prioridad — cosmético

| Gap | Esfuerzo |
|---|---|
| K. Dark/light mode toggle | 2h |
| L. Mobile-optimized vista | 6-8h refactor responsive |
| M. Localización ES/EN | 2-3h |

---

## PARTE 3 — Backend API status

### Endpoints existentes (`eolo-crop/main.py`, Flask)

| Endpoint | Method | Propósito | Status |
|---|---|---|---|
| `/` | GET | Home / redirect | ✅ |
| `/health` | GET | Cloud Run healthcheck | ✅ |
| `/status` | GET | Status JSON básico | ✅ |
| `/billing` | GET | Billing info (legacy) | ✅ |
| `/api/config` | POST | Edit config preferences (entry_hour, daily_loss_cap, max_positions) | ✅ |
| `/dashboard` | GET | Servir dashboard-crop.html | ✅ |
| `/api/state` | GET | Full state JSON (positions, KPIs, llm_metrics, overrides) | ✅ |
| `/daily-open-reset` | GET/POST | Trigger reset diario | ✅ |
| `/api/state/edit` | POST | Sprint S3: aplicar overrides | ✅ |

### Gaps backend identificados

#### Alta prioridad

| Gap | Por qué importa | Esfuerzo |
|---|---|---|
| **A. `/api/trades`** | Listar últimas N trades desde Firestore (decision_meta + tacit_rules) para frontend gap A | 1-2h |
| **B. `/api/llm/history`** | Últimas N decisiones del LLM con full meta. Hoy solo en logs de Cloud | 2h |
| **C. `/api/positions`** | Snapshot estructurado de positions abiertas (hoy en /api/state pero anidado) | 1h |
| **D. `/api/llm/reload_kb`** | UP-2.3 hot-reload del KB del LLM Engine — vive en engine, no en bot | 2h en LLM Engine |
| **E. `/api/alerts`** | Stream de eventos críticos para frontend gap D | 3-4h |

#### Media prioridad

| Gap | Esfuerzo |
|---|---|
| F. `/api/strategy/audit_log` | Historial cambios overrides | 2-3h |
| G. `/api/health/llm` | Healthcheck específico LLM Engine (separado del /health) | 1h |
| H. `/api/sector` | Sprint 12 SectorDir status per-ticker | 1-2h |
| I. `/api/snapshots/{date}` | Snapshots históricos para backtest viewer | 4-5h con storage |

#### Baja prioridad

| Gap | Esfuerzo |
|---|---|
| J. `/api/metrics/export` | Export Prometheus format de llm_metrics | 1h |
| K. `/api/version` | Endpoint con git commit, deploy timestamp, KB version | 30 min |
| L. WebSocket `/ws/state` | Push real-time state vs polling (cleaner UX) | 6-8h |

---

## PARTE 4 — Tech debts no listadas en backlog 29-may pero detectadas en code

Grep de `tech debt #` en código produce:

| Tech debt | Estado | Notas |
|---|---|---|
| #15 BVP/SVP rolling 100min | ⏳ Parcial | snapshot.py:38 daily_buffer mejorado pero notes mencionan defaults |
| #16 LLM snapshot lookback bumpeado 100→500 | ✅ Resuelto |  |
| #17 MACD 15m con pocos candles | ⏳ Pendiente | snapshot.py:298 — warn-once por ticker pero defaults |
| #18 VIX velocity buffer | ✅ Resuelto Sprint 6 |  |
| #20 vix_yesterday_close REST | ✅ Resuelto Sprint 7 |  |
| #21 Ventana parcial 2m/15m | ⏳ Pendiente | snapshot.py:334, 371 — defaults intencionales |
| #22 LLM Engine numeric keys | ✅ Resuelto Sprint 7 |  |
| #23 WS Schwab → REST polling | ✅ Resuelto Sprint 5 Fix B |  |
| #25 L1 quotes polling | ✅ Resuelto Sprint 5.B |  |

**Pendientes:** TD-15 (parcial), TD-17 (low priority, warn-once), TD-21 (defaults aceptables).

---

## PARTE 5 — Recomendaciones priorizadas

### Quick wins (1-2h cada uno, alto ROI)

| # | Sprint sugerido | Beneficio |
|---|---|---|
| 1 | Backend `/api/version` | Visibilidad commit/build/KB version. ~30 min |
| 2 | Backend `/api/positions` | Clean endpoint para frontend gap | ~1h |
| 3 | Frontend gap E: Cost trends chart | Reutiliza UP-2.2 backend, agrega histórico | ~1-2h |
| 4 | Backend `/api/health/llm` | Separar healthcheck LLM Engine para visualization | ~1h |

### Sprints medianos (3-5h, ROI alto)

| # | Sprint sugerido | Justificación |
|---|---|---|
| 5 | Backend `/api/trades` + Frontend gap A (trade detail expandido) | Convierte historial flat en investigable. Visibilidad LLM reasoning per-trade |
| 6 | Backend `/api/llm/history` + Frontend gap B (decision history) | Auditabilidad real del LLM. Útil para tunear KB v1.3 |
| 7 | Frontend gap C (KB inspection inline) | Acelera Sprint 18 — ver qué reglas se citan en real-time |
| 8 | Frontend gap D + Backend `/api/alerts` | Reduce dependencia de logs de Cloud para issues operacionales |

### Sprints grandes (>6h, ROI a futuro)

| # | Sprint sugerido | Cuándo |
|---|---|---|
| 9 | Frontend gap H + Backend `/api/strategy/audit_log` | Cuando haya múltiples usuarios o auditoría compliance |
| 10 | Backend `/api/snapshots/{date}` | Cuando UP-3.2 Backtest esté planeado |
| 11 | WebSocket /ws/state push | Cuando polling overhead se note (no urgente) |

---

## PARTE 6 — Qué NO está pendiente

- ✅ S3 stack completo
- ✅ LLM observability backend (Sprint 11 + UP-2.2 PR #27)
- ✅ Logging estructurado trades (Sprint 9+10+17)
- ✅ Cost tracking (Sprint 21 + PR #28 fix)
- ✅ Timezone consistency (Sprint 15 Fase E + Sprint 20)
- ✅ Persistence Firestore (Sprint S3.X)
- ✅ Ventana trading configurable
- ✅ VIX velocity correcta
- ✅ Sector field mapping correcto
- ✅ STOP_LOSS sin marks fantasma
- ✅ REST polling estable

**El bot es funcionalmente maduro.** Los gaps son **observabilidad + UX**, no operativos.

---

## TL;DR

**S3:** 100% LIVE. 12 sub-sprints, ~14 días de trabajo, override layer end-to-end + Firestore persistence.

**Frontend:** 11 secciones funcionales. **5 gaps de observabilidad alta prioridad** (trade detail, LLM history, KB inspection, alerts, cost trends).

**Backend:** 9 endpoints. **5 gaps alta prioridad** (`/api/trades`, `/api/llm/history`, `/api/positions`, `/api/llm/reload_kb`, `/api/alerts`).

**Próximos sprints recomendados (orden):**

1. **`/api/version`** (30 min, hygiene)
2. **`/api/trades` + frontend trade detail** (5h, ROI alto)
3. **`/api/llm/history` + frontend decision history** (5h, prepara Sprint 18)
4. **Cost trends chart** (1-2h, completa UP-2.2)
5. **`/api/positions`** (1h, clean abstraction)

Estos 5 cierran los gaps que más impactan productividad operacional sin agregar features mayores.
