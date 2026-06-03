

## 2026-06-01 Sesión nocturna (cierre)

Closed (3 fixes + 1 hotfix):
- #92 bump-version side-effect: param update_code_refs (commit a6d79e1)
- #93 Case #006 pnl_pct N/A counterfactual (commit 231c361)
- #95 Quant Data wire boundary at MarketSnapshot Pydantic (commit 0f77177)
  → LLM Engine redeploy: rev 00003-sqn → 00004-qw9
  → Schema MarketSnapshot: 57 fields → 68 fields (11 QD added)
  → OPS-3 Risk Arbiter ahora SÍ recibe Quant Data context en el prompt

Reconciliation v2.1 ↔ 06-01 master (docs/RECONCILIATION_v2_1_vs_06_01.md):
- 7 findings (A-G) identificados
- 6 decisiones cerradas (todas recomendadas aceptadas)
- Roadmap v2.2 4-6 sem: W1 Sprint 3 → W2 Sprint UP-1.4 (Sprint 18 + Sprint 2 v2.1 merge) → W3 S4 /juan/suggest → W4-W5 S5 backtest → W6 S7 manual close

Hallazgos críticos:
- A: Master 06-01 sec 8.1 dice Flask para LLM Engine → es FastAPI (patch en v2.2)
- C: Quant Data wire roto en boundary Pydantic — fix #95 cerró esto
- D: Schema MarketSnapshot pre-OPS-3 era raíz del bug
- E: Test gap engine para QD wiring — corregido con 2 tests nuevos en hotfix #95
- F: Docs referencian cloudbuild.yaml LLM Engine inexistente; pipeline real es bash deploy.sh
- G: test_llm_engine.py hardcodea KB_PATH v1.2.xlsx → futuro bump-version v1.3 lo rompe (tech debt, candidato hotfix simple)

Pending próxima sesión:
- Validation #77 + #95 runtime (automated 9:30 ET 2-jun) → grep [snapshot] en logs bot CROP
- Sprint 3 Rule Eval Trace (decision 7.3 A, no data-dep)
- Finding G hotfix (~10 min, oportunista)
- Investigación QD backlog API (paralelo, no-dev research)
- PROJECT_STATE.md rewrite (stale desde 31-may)

## 2026-06-03 — Reglas operativas Cowork ↔ Claude Code (sesión Juan)

**REGLA 1: NO recomendar después de dar opciones.**
Cuando Cowork ofrece a Juan opciones (1/2/3/A/B/C), terminar el mensaje ahí.
Sin "mi recomendación es...", sin "yo iría con X". Juan elige solo.
Razón: Juan tiene contexto operativo + apetito de riesgo que Cowork no tiene.

**REGLA 2: Maximizar trabajo de Claude Code, minimizar iteración cowork ↔ CC.**
- Sprints largos (>2h CC autónomo) en lugar de mini-tareas.
- Bloque CC debe ser end-to-end autónomo: edits + tests + commit + deploy + verify + report.
- Stops solo en hard failure (test fail, deploy fail, criterio explícito de seguridad).
- NO stops para clarification dentro del bloque — Cowork debe pensar el bloque
  completo antes y dar instrucciones unívocas; si hay ambigüedad real, decidir
  internamente y reportar en el commit/reporte.
- Decisiones internas autónomas: cuando CC encuentra un caso no previsto, decide
  con criterio (ej: "schema le=7 vs prompt le=45 → aplicar le=45 per Master Plan"),
  documenta en commit, sigue. No vuelve a preguntar a Juan.

**REGLA 3 (continuidad):**
- Schwab paper-trading only. PAPER_TRADING_ONLY=true. Nunca ejecutar trades reales.
- Canary deploys con tag + smoke + promote autónomo basado en criterios objetivos
  (HTTP 200 + zero ERROR logs últimos 5min). NO 100% WAIT como criterio bloqueante.
