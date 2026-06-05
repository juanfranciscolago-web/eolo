# Daily Process Framework v1

Source: PROCESO DIARIO.xlsx (Juan, 2026-06-04).

## Filosofía core

**"Siempre entrar, siempre controlar pérdidas"** — trade frequency > selectividad.
Risk control viene de stop loss + caps + anti-overtrade, NO de selectividad excesiva del LLM.

## Targets

- **Aspiracional**: $4,000/día → $880K/año (220 días)
- **Realista (modelo)**: $1,785/día normal × 4 días + $515 max loss × 1 día = $7,655/semana = $30,620/mes = $367K/año
- **Comisiones**: $0.65/contrato × 40 contratos × 8 entries = $208/día gross, $26/día neto (orders no contratos por trade)

## Reglas duras (PROHIBITIVA)

- TR-Juan-081: NEVER_OVERNIGHT_HARD
- TR-Juan-084: STOP_LOSS_200_LIMIT_250
- TR-Juan-085: MAX_CONTRACTS_PER_SIDE_30 (15+15 en 2 strikes)
- TR-Juan-056: 0DTE post-15:30 ET BLOCK

## Daily process — 8 entries normal day

| Entry # | Contratos | Premium | Profit | Notas |
|---------|-----------|---------|--------|-------|
| 1 | 10 | $40 | $370 | Apertura primera entry |
| 2 | 5 | $45 | $195 | Distribución conservadora |
| 3 | 15 | $40 | $570 | Confianza aumentada con confirmación |
| 4 | 10 | $40 | $370 | Última entry inicial |
| 5 | 10 | -$10 | -$130 | RECOMPRA (si stop hit) |
| 6 | 10 | -$10 | -$130 | RECOMPRA segunda |
| 7 | 10 | $30 | $270 | RE-ENTRY post-recompra |
| 8 | 10 | $30 | $270 | RE-ENTRY segunda |
| **TOTAL** | | | **$1,785** | |

## Daily process — 8 entries max loss day

Stop loss en Entry 5, recompra y re-entries con tamaño real (no 0):

| Entry # | Contratos | Premium | Profit | Notas |
|---------|-----------|---------|--------|-------|
| 1-4 | 10/5/15/10 | $40/$45/$40/$40 | $1,505 | Compras iniciales OK |
| 5 (STOP) | 10 | -$60 | -$630 | Stop loss 200% gatillado |
| 6 | 10 | -$10 | -$130 | Recompra del strike afectado |
| 7 (RE-ENTRY) | 10 | $30 | $270 | Re-entry nuevo strike |
| 8 | 10 | $30 | $270 | Re-entry segunda |
| **TOTAL MAX LOSS DAY** | | | **~$1,285** | (positivo, no pérdida real) |

## Decision tree LLM

1. Verificar PROHIBITIVAs (TR-Juan-056, 072, 081, 084, 085) → si gatilladas: BLOCK_HARD
2. Determinar DTE (TR-Juan-082): 0 = THETA mode, 1-4 = TREND mode
3. Determinar VIX direction (TR-Juan-083): exclude primeros 10min, threshold 3% en 5min
4. Verificar régimen (TR-Juan-077 OR + TR-Juan-079 multi-confluence)
5. Calcular sizing según Entry # del daily process (TR-Juan-087)
6. Emitir verdict (default action, no WAIT salvo PROHIBITIVA o evento)

## Casos de WAIT permitidos

Solo:
- VIX EXPANDING >+10% en 30min (régimen panic, no es operativo normal)
- Evento macro <60min (FOMC, CPI, NFP confirmado en calendario)
- PROHIBITIVA explícita
- Mercado halt
- API down

## Casos GOLD reference

- CASE-Juan-001 (Friday EOM SPX): IC simétrico, gamma_zero alignment
- CASE-Juan-002 (Monday SPX): IC + scaling puts on dip pattern
- CASE-Juan-003 (Tuesday SPY): Bottom detection at OR pivot

## Deploy flow (sprint AUTO-CANARY-PROMOTE)

Para evitar 503 "no snapshot cached" en bot canary deploys:

1. **Build + canary deploy** (`gcloud builds submit` + `gcloud run deploy --no-traffic --tag=tNN`)
2. **Smoke tests** contra tag URL (endpoints simples respondan; no requiere cache)
3. **Promote con warmup automático**:
   ```bash
   ./tools/promote_canary.sh tNN 300 900
   ```
   Args: tag, warmup_sec, max_wait_sec. El script espera 5 min warmup + verifica
   `_last_snapshots` cache cada 60s (vía `/api/state`), max 15 min total. Si cache
   OK → promote 100% + cleanup tag previo. Si timeout → alerta y NO promote (exit 1).

### Schedule diferido para market-open promote

Si querés promover el canary mañana antes del open (e.g. 9:25 ET para que esté
caliente al market open 9:30 ET):

```bash
# Calcular segundos hasta target ET:
SECS=$(python3 -c "
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
et = ZoneInfo('America/New_York')
now = datetime.now(et)
target = now.replace(hour=9, minute=25, second=0, microsecond=0)
if target <= now: target += timedelta(days=1)
print(int((target - now).total_seconds()))
")

LOG="/tmp/promote_t23_$(date +%Y%m%d_%H%M%S).log"
nohup bash -c "sleep $SECS && cd ~/PycharmProjects/eolo && ./tools/promote_canary.sh t23 300 1200" > $LOG 2>&1 &
disown
echo "Scheduled. Log: $LOG  PID: $!"
```

Caveat: el `nohup` corre en tu Mac local. Si se cierra sesión o sleep largo
suspende el proceso, no se ejecuta. Para schedule reliable: usar `launchd`
plist o un Cloud Scheduler + Cloud Function que ejecute el promote remoto.

### Por qué importa
El bot lazy-inicializa `_last_snapshots` cuando arranca el polling loop. Un
fresh container necesita ~30-90s después del start para tener snapshots cached.
Si se promotea antes: `/juan/suggest` retorna 503 "no snapshot cached for {ticker}".

### Próximo paso (futuro)
Integrar `tools/promote_canary.sh` directamente en `cloudbuild-buildonly.yaml`
o en `eolo-crop/deploy.sh` para que el promote sea siempre post-warmup.
Alternativa robusta: warmup endpoint explícito en bot que pre-popula el cache
on-demand antes del promote.
