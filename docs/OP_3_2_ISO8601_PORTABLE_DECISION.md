# OP-3.2 — Sprint 15 Opción B ISO 8601 Portable: Decisión

**Fecha:** 2026-05-31 (Domingo cierre)
**Backlog ref:** OP-3.2 MEDIO — "Sprint 15 Opción B portable (ISO 8601 + dashboard render)"
**Verdict:** **DEFER (no ejecutar ahora). Triggers documentados para reconsider.**

---

## TL;DR

| Pregunta | Respuesta |
|---|---|
| ¿Sprint 15 Opción A funciona? | Sí, deployado y validated |
| ¿Hay problema reportado de timezone display? | No detectado |
| ¿Justifica 2h de refactor? | NO por ahora |
| ¿Cuándo reconsider? | Si Juan usa el dashboard desde una zona horaria != ET, o si llega user 3ero |

---

## Estado actual — Sprint 15 Opción A (deployed)

### Backend emit format

`eolo-crop/llm_gate/snapshot.py:195` y otros 7+ lugares:

```python
snapshot["timestamp"] = datetime.now(ZoneInfo("America/New_York")).isoformat()
# Result: "2026-06-01T09:30:00-04:00" (con offset ET, no Z)
```

Otros lugares: `trade_logger.py`, `execution/options_trader.py`, `crop_main.py` (3 puntos).

### Dashboard parse format

`eolo-crop/dashboard-crop.html:2978` y otros:

```javascript
const et = new Date(d.toLocaleString('en-US', { timeZone: 'America/New_York' }));
```

### Funcionamiento end-to-end

1. Backend escribe timestamps en ET con offset (`-04:00` o `-05:00` según DST)
2. Browser recibe el string, parsea como Date
3. Dashboard convierte a ET para display
4. **Resultado:** display correcto en ET, independiente de timezone del browser (porque el dashboard fuerza ET en `toLocaleString`)

### Por qué funciona

Aún si el browser está en ART, BRT, PST, etc., el `toLocaleString('en-US', {timeZone: 'America/New_York'})` lo convierte a ET. La Opción A NO depende de timezone del cliente.

---

## Opción B propuesta (no implementada)

### Backend emit format propuesto

```python
snapshot["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
# Result: "2026-06-01T13:30:00Z" (UTC con Z, standard ISO 8601)
```

### Dashboard parse format

Idéntico al actual:

```javascript
const et = new Date(d.toLocaleString('en-US', { timeZone: 'America/New_York' }));
```

No cambia el frontend.

### Diferencia funcional

| Aspecto | Opción A (current) | Opción B (proposed) |
|---|---|---|
| Backend timezone | ET con offset | UTC con Z |
| Frontend parse | Idéntico | Idéntico |
| Display final | ET correcto | ET correcto |
| Standard compliance | ISO 8601 con offset (válido) | ISO 8601 UTC con Z (más común) |
| Storage portability | ET con offset embebido | UTC absoluto |
| Logs portability | Mismo display, distinto formato raw | UTC más portable para tools |

---

## Análisis costo-beneficio

### Costo de implementar Opción B

| Componente | Esfuerzo |
|---|---|
| Refactor 8+ emit points en backend (Python) | 1-1.5h |
| Validar dashboard sigue funcionando | 30 min |
| Validar Firestore timestamps consistentes con doc legacy | 30 min |
| Deploy + smoke | 15 min |
| **Total** | **2-3h** |

### Beneficio de Opción B

1. **Portability:** logs y Firestore docs en UTC son más universales para tools (Grafana, BigQuery, scripts ad-hoc)
2. **Standard compliance:** ISO 8601 con `Z` es más común en APIs públicas
3. **Backups portables:** si exportás Firestore data, UTC es más facil de procesar
4. **Sin DST ambiguity:** UTC no tiene offset cambiando entre invierno/verano

### Costo de NO implementar (mantener Opción A)

1. **Logs raw con offset ET:** análisis ad-hoc require mental conversion
2. **Firestore queries por timestamp:** menos eficientes (rango ET en lugar de UTC absoluto)
3. **Si migrate a Grafana/BigQuery:** require ETL para convertir a UTC
4. **Riesgo DST edge case:** durante transición DST (Mar/Nov), 1 hora puede ser ambigua

### Beneficio de NO implementar

1. **0 esfuerzo adicional**
2. **0 risk de breaking dashboard** durante refactor
3. **Funciona hoy correctamente** para el use case actual (Juan single user en ET)

---

## Decisión recomendada

### **DEFER (mantener Opción A) por ahora.**

Razones:

1. **No hay problema reportado.** Dashboard renders correctly. Logs son interpretables (Juan en ET).
2. **Sprint 15 Opción A "resuelve el síntoma".** El backlog mismo lo reconoce.
3. **Saturation cognitiva real esta noche.** 7 PRs draft ya pendientes. Agregar uno más sin beneficio crítico baja el ROI marginal.
4. **Refactor sin tests automatizados.** El dashboard es 5500+ LOC, los emit points en backend son 8+. Risk de regression sutil si algo se escapa.
5. **No bloquea Sprint 18 ni operación normal.**

### Triggers para RECONSIDERAR

Ejecutar OP-3.2 si:

1. **User 3ero accede al dashboard** desde zona horaria != ET y reporta confusión
2. **Setup de Grafana / BigQuery** require UTC para queries eficientes
3. **DST transition** causa edge case observable (ej. 1 trade aparece como 2 entries o vice versa por la hora ambigua en Nov)
4. **Migration a Cloud SQL / Postgres** donde timestamp WITH TIME ZONE storage requiera UTC convention
5. **Tiempo libre disponible** (sprint sin item más prioritario)

### Si se decide ejecutar (referencia futura)

Plan:

```bash
cd ~/PycharmProjects/eolo
git worktree add ../eolo-iso8601 -b fix/iso8601-portable
# Worktree dedicado

# Modificar 8 emit points en backend:
# - eolo-crop/llm_gate/snapshot.py:195
# - eolo-crop/llm_gate/trade_logger.py:* (líneas con datetime.now(ET).isoformat())
# - eolo-crop/execution/options_trader.py:158
# - eolo-crop/crop_main.py:1695, 1756, 1770, 3012, 3081, 3144 (audit grep)
# Cambiar de:
#     datetime.now(ZoneInfo("America/New_York")).isoformat()
# A:
#     datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# Test:
python3 -m py_compile eolo-crop/llm_gate/snapshot.py eolo-crop/crop_main.py
# Servir dashboard local con datos UTC y verificar render

# Commit + PR draft
```

Esfuerzo: 2-3h Claude Code session.

---

## Backlog impact

OP-3.2 backlog item reescrito:

```
ANTES (29-may):
  OP-3.2 MEDIO — Sprint 15 Opción B portable (ISO 8601 + dashboard render)
  Esfuerzo: 1-2h
  Decisión pendiente: ¿Justifica el costo si Opción A ya resuelve el síntoma?

DESPUÉS (31-may):
  OP-3.2 — Sprint 15 Opción B portable — DEFER
  Verdict: NO ejecutar hasta trigger (user 3ero, Grafana/BigQuery setup, DST edge case)
  Plan ejecutable documentado: docs/OP_3_2_ISO8601_PORTABLE_DECISION.md
  Esfuerzo cuando se ejecute: 2-3h
```

---

**Decisión: DEFER. Opción A funciona. Plan documentado para ejecución futura cuando trigger amerite.**
