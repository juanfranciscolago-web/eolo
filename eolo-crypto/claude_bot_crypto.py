# ============================================================
#  EOLO Crypto — Claude Bot (Estrategia #14 adaptada a crypto)
#
#  Motor autónomo con Anthropic API que decide BUY / SELL / HOLD
#  para los pares activos de Binance.
#
#  Diferencias vs eolo-options/claude/claude_bot.py:
#    - Contexto crypto: BTC dominance, 24h volume, velas 1h,
#      funding rate (si es futures; en spot lo omitimos), cambios
#      recientes de sentimiento/pumps.
#    - Horario: 24/7 UTC, no session-aware.
#    - Ejecuta sobre BinanceExecutor (paper/testnet/live).
#    - Namespace Firestore separado: eolo-crypto-claude-decisions.
# ============================================================
import asyncio
import json
import os
import time
import traceback
from datetime import datetime, timezone

from loguru import logger

try:
    import anthropic
except ImportError:
    anthropic = None

import settings
from helpers import get_anthropic_api_key, firestore_write
from runtime_config import config as runtime_config


MAX_TOKENS  = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "512"))   # era 1024 — el JSON rara vez excede 400 tokens
TEMPERATURE = 0.1

SYSTEM_PROMPT = """You are Claude Bot, an autonomous crypto trading agent running on Binance spot.

Your job: every cycle, given the current state of the portfolio + market, decide whether to
BUY a new position, SELL an existing one, or HOLD. You operate in 24/7 markets and must
consider: BTC dominance, broader market tone, per-pair momentum, volume, and risk exposure.

Constraints:
- You only operate SPOT (no leverage, no shorts).
- Max concurrent positions: {max_positions}.
- Position sizing is handled by the executor — you only output the decision.
- The executor will reject orders that violate min-notional or step-size rules, so
  focus on the *signal*, not the quantity.
- Confidence below 0.60 → always HOLD (don't force trades).

Return ONLY a valid JSON object with this exact schema:
{{
  "action": "BUY" | "SELL" | "HOLD",
  "symbol": "BTCUSDT" | null,
  "confidence": 0.0-1.0,
  "reason": "short explanation (one sentence)",
  "stop_loss_pct": 1.0-10.0,
  "take_profit_pct": 1.0-20.0
}}

Do NOT include markdown fences, comments, or any text outside the JSON."""


class ClaudeBotCrypto:
    """
    Corre cada CLAUDE_INTERVAL_SEC. Llama a Claude con un snapshot
    del mercado crypto y traduce la respuesta en una señal ejecutable.
    """

    def __init__(self, buffer, executor, state=None):
        self.buffer   = buffer       # MarketDataBuffer
        self.executor = executor     # BinanceExecutor
        self._state   = state        # StateWriter (opcional, para gate de costos)
        self._client  = None
        self._running = False
        self._today_cost_usd = 0.0
        self._today_date = datetime.now(timezone.utc).date()
        self._decision_handlers = []   # fn(decision_dict)  — sync o async
        # Gate de costos: saltar el ciclo si no hay BUY/SELL recientes de
        # estrategias técnicas ni posiciones abiertas. Override: CLAUDE_GATE=0.
        self._gate_enabled = os.environ.get("CLAUDE_GATE", "1") != "0"
        # Ventana de búsqueda de signals recientes (en segundos) — si hubo
        # señal en los últimos N segundos, el ciclo se considera accionable.
        self._gate_lookback_sec = int(
            os.environ.get("CLAUDE_GATE_LOOKBACK_SEC", "1800")  # 30 min
        )
        self._init_client()

    def add_decision_handler(self, fn):
        """Registra un callback que recibe cada decisión Claude (dict)."""
        self._decision_handlers.append(fn)

    def _init_client(self):
        if anthropic is None:
            logger.warning("[CLAUDE-CRYPTO] anthropic no instalado — Claude Bot deshabilitado")
            return
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            try:
                key = get_anthropic_api_key()
            except Exception as e:
                logger.warning(f"[CLAUDE-CRYPTO] No pude leer ANTHROPIC_API_KEY: {e}")
                return
        if not key:
            return
        self._client = anthropic.Anthropic(api_key=key)
        logger.info(f"[CLAUDE-CRYPTO] Cliente iniciado con modelo {settings.CLAUDE_MODEL}")

    def stop(self):
        self._running = False

    # ── Loop principal ────────────────────────────────────

    async def start(self):
        if self._client is None:
            logger.warning("[CLAUDE-CRYPTO] Cliente no inicializado — no corre.")
            return

        self._running = True
        logger.info(
            f"[CLAUDE-CRYPTO] Arrancando | interval={settings.CLAUDE_INTERVAL_SEC}s "
            f"| max_cost/day=${runtime_config.claude_max_cost_per_day} "
            f"| enabled={runtime_config.claude_bot_enabled}"
        )

        # Nota: el toggle claude_bot_enabled se consulta EN CADA CICLO
        # (no sólo al arranque) para que el /api/toggle-claude del dashboard
        # tenga efecto sin redeploy.
        while self._running:
            try:
                self._rollover_day()
                if not runtime_config.claude_bot_enabled:
                    # Deshabilitado en runtime — idle hasta que el dashboard
                    # lo reactive. Chequeo barato cada 10s.
                    await asyncio.sleep(10)
                    continue
                budget = runtime_config.claude_max_cost_per_day
                if self._today_cost_usd < budget:
                    await self._cycle()
                else:
                    logger.warning(
                        f"[CLAUDE-CRYPTO] Presupuesto diario agotado "
                        f"(${self._today_cost_usd:.2f}/${budget:.2f}) — saltando ciclo"
                    )
            except Exception as e:
                logger.error(
                    f"[CLAUDE-CRYPTO] Error en ciclo: {type(e).__name__}: {e}\n"
                    f"{traceback.format_exc()}"
                )

            # Espera interruptible
            for _ in range(settings.CLAUDE_INTERVAL_SEC * 10):
                if not self._running:
                    break
                await asyncio.sleep(0.1)

    def _rollover_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self._today_date:
            logger.info(
                f"[CLAUDE-CRYPTO] Rollover día {self._today_date} → {today} | "
                f"gastado ayer: ${self._today_cost_usd:.2f}"
            )
            self._today_date = today
            self._today_cost_usd = 0.0

    def _is_actionable_window(self) -> bool:
        """
        Decide si vale la pena llamar a Claude en este ciclo:
          - True si hay alguna BUY/SELL de estrategia técnica en los últimos
            `_gate_lookback_sec` segundos, o
          - True si hay al menos una posición abierta (Claude puede querer
            cerrarla o reevaluarla).
          - False si mercado lateral y sin exposición.
        """
        # 1) Posiciones abiertas → siempre evaluar
        try:
            positions = self.executor.get_open_positions() or {}
            if positions:
                return True
        except Exception:
            # Si no podemos leer posiciones, no bloqueamos Claude
            return True

        # 2) Signals recientes en state.last_signals (lo escribe orquestador)
        try:
            last_signals = self._state.get_signals(limit=20)
        except AttributeError:
            # Fallback si StateWriter no expone getter — acceso directo
            last_signals = (getattr(self._state, "_state", {}) or {}).get("last_signals", [])[:20]
        except Exception:
            return True  # error leyendo → fail-open (no bloquear Claude)

        if not last_signals:
            return False

        cutoff = datetime.now(timezone.utc).timestamp() - self._gate_lookback_sec
        for s in last_signals:
            if (s or {}).get("signal") not in ("BUY", "SELL"):
                continue
            ts = s.get("ts")
            ts_epoch = None
            if isinstance(ts, (int, float)):
                ts_epoch = float(ts)
            elif isinstance(ts, str):
                try:
                    ts_epoch = datetime.fromisoformat(
                        ts.replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    ts_epoch = None
            if ts_epoch is None or ts_epoch >= cutoff:
                # Sin ts parseable asumimos reciente (fail-open)
                return True
        return False

    async def _cycle(self):
        snapshot = self._build_snapshot()
        if not snapshot["pairs"]:
            logger.debug("[CLAUDE-CRYPTO] Snapshot vacío — nada que evaluar")
            return

        # Gate de costos: si no hubo signals BUY/SELL en los últimos N segs
        # y no hay posiciones abiertas, saltar la llamada Claude.
        # Crypto es 24/7 pero la mayoría del tiempo está lateral — sin gate
        # cada ciclo gasta tokens aunque no pase nada. Bajar el consumo ~80%
        # en sesiones tranquilas.
        if self._gate_enabled and self._state is not None:
            if not self._is_actionable_window():
                logger.debug(
                    "[CLAUDE-CRYPTO] Gate: sin signals BUY/SELL recientes "
                    "ni posiciones abiertas — skip ciclo (ahorro API)"
                )
                return

        decision, cost = await self._call_claude(snapshot)
        self._today_cost_usd += cost

        self._log_decision(decision, cost)

        # Notificar a los handlers registrados (p.ej. state_writer.push_decision)
        for fn in self._decision_handlers:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(decision)
                else:
                    fn(decision)
            except Exception as e:
                logger.warning(
                    f"[CLAUDE-CRYPTO] decision_handler {getattr(fn, '__name__', fn)} "
                    f"falló: {type(e).__name__}: {e}"
                )

        # Ejecutar decisión si pasa filtros
        if decision.get("action") == "BUY" and decision.get("confidence", 0) >= 0.60:
            symbol = decision.get("symbol")
            price = self.buffer.latest_close(symbol) if symbol else None
            if symbol and price:
                self.executor.open_long(
                    symbol=symbol,
                    price=price,
                    strategy="claude_bot",
                    reason=decision.get("reason", ""),
                )
        elif decision.get("action") == "SELL":
            symbol = decision.get("symbol")
            price = self.buffer.latest_close(symbol) if symbol else None
            if symbol and price:
                self.executor.close_long(
                    symbol=symbol,
                    price=price,
                    strategy="claude_bot",
                    reason=decision.get("reason", ""),
                )

    # ── Snapshot building ─────────────────────────────────

    def _build_snapshot(self) -> dict:
        """
        Construye el contexto de mercado que se manda a Claude.
        Para cada símbolo activo: último precio, cambio 24h, velas 1h
        recientes (resample de 1m → 1h), y posición abierta si aplica.
        """
        positions = self.executor.get_open_positions()
        balance   = self.executor.get_balance_usdt()

        pairs = []
        for symbol in self.buffer.symbols():
            df = self.buffer.as_dataframe(symbol)
            if df is None or len(df) < 60:
                continue

            # pandas 2.2+ deprecó "H" (mayúscula) en favor de "h" (minúscula)
            df_1h = df[["open", "high", "low", "close", "volume"]].resample("1h").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum",
            }).dropna().tail(24)

            last_close = float(df["close"].iloc[-1])
            open_24h   = float(df["close"].iloc[-min(1440, len(df))])  # aprox 24h atrás
            change_pct = (last_close / open_24h - 1.0) * 100 if open_24h > 0 else 0.0

            entry = {
                "symbol":        symbol,
                "last_price":    last_close,
                "change_24h_pct": round(change_pct, 2),
                "volume_24h":    float(df["volume"].tail(1440).sum()),
                "candles_1h":    [
                    {
                        "t": ts.strftime("%Y-%m-%dT%H:%M"),
                        "o": round(float(row["open"]), 6),
                        "h": round(float(row["high"]), 6),
                        "l": round(float(row["low"]),  6),
                        "c": round(float(row["close"]),6),
                        "v": round(float(row["volume"]), 2),
                    }
                    for ts, row in df_1h.iterrows()
                ],
                "position":      positions.get(symbol) or positions.get(symbol.replace("USDT","")),
            }
            pairs.append(entry)

        return {
            "utc_time":           datetime.now(timezone.utc).isoformat(),
            "mode":               settings.BINANCE_MODE,
            "balance_usdt":       round(balance, 2),
            "open_positions":     len(positions),
            "max_positions":      runtime_config.max_open_positions,
            "position_size_pct":  runtime_config.position_size_pct,
            "pairs":              pairs,
        }

    # ── Call Claude ───────────────────────────────────────

    async def _call_claude(self, snapshot: dict) -> tuple[dict, float]:
        """Retorna (decision_dict, approx_cost_usd)."""
        user_content = (
            "Market snapshot (JSON):\n\n```json\n"
            + json.dumps(snapshot, default=str)
            + "\n```\n\nDecide and respond with JSON only."
        )

        loop = asyncio.get_event_loop()

        def _sync_call():
            return self._client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system=SYSTEM_PROMPT.format(max_positions=runtime_config.max_open_positions),
                messages=[{"role": "user", "content": user_content}],
            )

        response = await loop.run_in_executor(None, _sync_call)
        text = response.content[0].text if response.content else "{}"

        # Parsing robusto: quitar fences si los metió pese al prompt
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            decision = json.loads(text.strip())
        except json.JSONDecodeError:
            logger.warning(f"[CLAUDE-CRYPTO] Respuesta no JSON: {text[:200]}")
            decision = {"action": "HOLD", "confidence": 0.0, "reason": "parse_error"}

        # Estimate cost: Sonnet 4.6 ~ $3/M input, $15/M output
        usage = getattr(response, "usage", None)
        if usage:
            cost = (usage.input_tokens * 3 / 1e6) + (usage.output_tokens * 15 / 1e6)
        else:
            cost = 0.01  # fallback conservador

        return decision, cost

    # ── Logging ───────────────────────────────────────────

    def _log_decision(self, decision: dict, cost: float):
        ts = datetime.now(timezone.utc)
        try:
            firestore_write(
                settings.FIRESTORE_CLAUDE_COLLECTION,
                f"{ts.strftime('%Y-%m-%d')}-{int(ts.timestamp()*1000)}",
                {
                    "ts_iso":     ts.isoformat(),
                    "action":     decision.get("action"),
                    "symbol":     decision.get("symbol"),
                    "confidence": decision.get("confidence"),
                    "reason":     decision.get("reason"),
                    "stop_loss_pct":   decision.get("stop_loss_pct"),
                    "take_profit_pct": decision.get("take_profit_pct"),
                    "approx_cost_usd": round(cost, 5),
                    "mode":       settings.BINANCE_MODE,
                },
            )
        except Exception as e:
            logger.warning(f"[CLAUDE-CRYPTO] Firestore log falló: {e}")

        logger.info(
            f"[CLAUDE-CRYPTO] decision: {decision.get('action')} "
            f"{decision.get('symbol') or '-'} "
            f"conf={decision.get('confidence', 0):.2f} "
            f"cost=${cost:.4f} | {decision.get('reason', '')}"
        )
