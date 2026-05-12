# ============================================================
#  EOLO v2 — Claude Bot (Estrategia #14 — acciones del underlying)
#
#  Motor de decisión autónomo basado en la API de Anthropic.
#  Corre en paralelo con las 13 estrategias técnicas y con
#  OptionsBrain (que opera opciones). Este engine opera
#  ACCIONES del underlying (SOXL, TSLL, SPY, QQQ...), igual
#  que la versión de v1.2, pero dentro del runtime de Eolo v2.
#
#  Cada ciclo (~60s):
#    1. Construye un snapshot del mercado (precios, velas,
#       indicadores básicos, señales recientes, posiciones abiertas).
#    2. Llama a Claude (Sonnet 4.6 por default) y le pide
#       una decisión BUY / SELL / HOLD para algún ticker.
#    3. Parsea el JSON estructurado y devuelve una señal
#       con el mismo formato que usan las otras estrategias.
#    4. Registra cada decisión en Firestore
#       (eolo-claude-bot-decisions-v2/{YYYY-MM-DD}/decisions/{ts}).
#
#  Modo paper: por default NO ejecuta órdenes. Solo registra
#  decisiones para evaluar performance antes de pasar a live.
#
#  Namespace Firestore separado de OptionsBrain:
#    - OptionsBrain → eolo-claude-decisions-v2
#    - Claude Bot   → eolo-claude-bot-decisions-v2
# ============================================================
import os
import json
import time
import asyncio
import traceback
from datetime import datetime, timezone

from loguru import logger

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from google.cloud import secretmanager
except ImportError:
    secretmanager = None


# ── Configuración ─────────────────────────────────────────
# Haiku 4.5 es ~10-15x más barato que Sonnet 4.6 y entrega calidad suficiente
# para decisiones estructuradas JSON (BUY/SELL/HOLD + reasoning). Si querés
# volver a Sonnet, setear ANTHROPIC_MODEL en env o cambiar acá.
DEFAULT_MODEL   = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
# 512 tokens alcanza para el JSON de decisión + narrativa breve. Antes era 1024.
MAX_TOKENS      = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "512"))
TEMPERATURE     = 0.1          # decisiones estables/deterministas
MIN_CONFIDENCE_FOR_EXECUTION = 0.60   # en modo live, umbral mínimo

# Valores admitidos para el campo `signal` en las señales de Eolo
VALID_SIGNALS = {"BUY", "SELL", "HOLD"}


# ── Helpers para cargar la API key ────────────────────────

def _load_api_key_from_secret_manager(project_id: str, secret_name: str = "ANTHROPIC_API_KEY") -> str | None:
    """Carga ANTHROPIC_API_KEY desde Google Secret Manager.
    Devuelve None si no se puede leer (logueará el error, no crashea)."""
    if secretmanager is None:
        return None
    try:
        client = secretmanager.SecretManagerServiceClient()
        name   = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        logger.warning(f"[CLAUDE_BOT_V2] No pude leer {secret_name} de Secret Manager: {e}")
        return None


def _get_api_key() -> str | None:
    """Devuelve la API key desde (en este orden):
       1. env ANTHROPIC_API_KEY
       2. Google Secret Manager (secreto ANTHROPIC_API_KEY en el project activo)
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if project:
        return _load_api_key_from_secret_manager(project)
    return None


# ── Engine ────────────────────────────────────────────────

class ClaudeBotEngine:
    """
    Motor autónomo de decisión — versión Eolo v2.
    Safe by default: en paper mode (flag `paper_mode=True`) solo
    registra decisiones, no ejecuta.

    Opera sobre ACCIONES del underlying (no opciones).
    OptionsBrain se ocupa en paralelo del lado de opciones.
    """

    def __init__(self, model: str = DEFAULT_MODEL, paper_mode: bool = True):
        if anthropic is None:
            raise ImportError("Falta el paquete `anthropic`. Agregalo a requirements.txt")

        api_key = _get_api_key()
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY no disponible ni en env ni en Secret Manager — "
                "configurá el secreto con: gcloud secrets create ANTHROPIC_API_KEY --data-file=-"
            )

        self._client       = anthropic.Anthropic(api_key=api_key)
        self._model        = model
        self._paper_mode   = paper_mode
        self._call_count   = 0
        self._last_error   = None
        self._last_call_ts = 0.0

    # ── API pública ──────────────────────────────────────

    async def decide(self, snapshot: dict) -> dict:
        """
        Entrada: `snapshot` con todo el contexto del mercado.
          Campos esperados (tolera missing):
            - tickers: list[str]
            - prices:  dict[ticker → float]
            - candles_summary: dict[ticker → dict] con {open, high, low, close, trend_1m, trend_5m, ...}
            - recent_signals: list[dict] — últimas señales de las 13 estrategias hoy
            - open_positions: list[dict] — [{ticker, entry, unrealized_pnl_pct}, ...]
            - budget: float
            - market_time_et: str
            - stats_today: {trades_today, signals_today, total_pnl, win_rate}

        Salida: dict con la decisión normalizada:
            {
              "ticker": str,
              "signal": "BUY" | "SELL" | "HOLD",
              "price":  float,
              "reasoning": str,
              "confidence": float (0..1),
              "stop_loss_pct": float (porc. del precio entrada),
              "take_profit_pct": float,
              "strategy_used": str (narrativa breve),
              "candle_time": str,
              "raw": str (respuesta cruda para debug),
            }
        """
        t0 = time.time()
        prompt = self._build_prompt(snapshot)

        try:
            # La librería anthropic es sync — corremos en threadpool.
            raw = await asyncio.to_thread(self._call_claude, prompt)
            self._call_count   += 1
            self._last_call_ts  = time.time()
            decision = self._parse_response(raw)
            decision["latency_s"] = round(time.time() - t0, 2)
            decision["model"]     = self._model
            decision["raw"]       = raw[:2000]
            return decision

        except Exception as e:
            self._last_error = str(e)
            logger.error(f"[CLAUDE_BOT_V2] Error llamando a Claude: {e}\n{traceback.format_exc()}")
            return {
                "ticker":    snapshot.get("tickers", ["SPY"])[0] if snapshot.get("tickers") else "SPY",
                "signal":    "HOLD",
                "price":     0.0,
                "reasoning": f"Error en Claude API: {e}",
                "confidence": 0.0,
                "stop_loss_pct":   0.0,
                "take_profit_pct": 0.0,
                "strategy_used":   "error",
                "candle_time":     datetime.now(timezone.utc).isoformat(),
                "latency_s":       round(time.time() - t0, 2),
                "model":           self._model,
                "raw":             "",
                "error":           str(e),
            }

    # ── Construcción del prompt ──────────────────────────

    def _build_prompt(self, snap: dict) -> str:
        ticks   = snap.get("tickers")      or []
        prices  = snap.get("prices")       or {}
        cs      = snap.get("candles_summary") or {}
        sigs    = snap.get("recent_signals")  or []
        poss    = snap.get("open_positions")  or []
        budget  = snap.get("budget", 100)
        mkt_time = snap.get("market_time_et") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M ET")
        stats   = snap.get("stats_today") or {}

        # ── Tabla de precios actuales ─────────────────
        price_lines = []
        for t in ticks:
            p = prices.get(t)
            c = cs.get(t, {}) or {}
            o = c.get("open"); h = c.get("high"); l = c.get("low"); cl = c.get("close")
            t1 = c.get("trend_1m");  t5 = c.get("trend_5m");  t15 = c.get("trend_15m")
            vol  = c.get("volume")
            pct  = c.get("change_pct_open")
            price_lines.append(
                f"  {t:<6} | price=${p if p else 'n/a'} "
                f"| O=${o} H=${h} L=${l} C=${cl} Δ={pct}% "
                f"| trend 1m/5m/15m = {t1}/{t5}/{t15} "
                f"| vol={vol}"
            )
        prices_text = "\n".join(price_lines) if price_lines else "  (sin datos)"

        # ── Últimas señales de las 13 estrategias ────
        if sigs:
            sig_lines = []
            for s in sigs[-20:]:   # últimas 20
                sig_lines.append(
                    f"  [{s.get('ts','?')}] {s.get('strategy','?'):<18} "
                    f"{s.get('ticker','?'):<6} → {s.get('signal','?'):<4} "
                    f"@ ${s.get('price','?')}"
                )
            signals_text = "\n".join(sig_lines)
            buy_n  = sum(1 for s in sigs if s.get("signal") == "BUY")
            sell_n = sum(1 for s in sigs if s.get("signal") == "SELL")
        else:
            signals_text = "  (ninguna señal en la ventana reciente)"
            buy_n = sell_n = 0

        # ── Posiciones abiertas ──────────────────────
        if poss:
            pos_lines = []
            for p in poss:
                pnl = p.get("unrealized_pnl_pct")
                pos_lines.append(
                    f"  {p.get('ticker'):<6} entry=${p.get('entry')} "
                    f"current=${p.get('current')} "
                    f"pnl={pnl:+.2f}%" if isinstance(pnl, (int, float)) else
                    f"  {p.get('ticker'):<6} entry=${p.get('entry')} "
                    f"current=${p.get('current')} pnl=n/a"
                )
            positions_text = "\n".join(pos_lines)
        else:
            positions_text = "  (sin posiciones abiertas)"

        # ── Stats del día ────────────────────────────
        stats_text = (
            f"  trades_today={stats.get('trades_today', 0)}  "
            f"signals_today={stats.get('signals_today', 0)}  "
            f"total_pnl=${stats.get('total_pnl', 0)}  "
            f"win_rate={stats.get('win_rate', 'n/a')}"
        )

        prompt = f"""Sos el motor de decisión autónomo "Claude Bot" dentro del sistema EOLO v2, un bot de day-trading multi-activo (acciones + opciones) en US equities.

Tu rol: sos la **estrategia #14** que corre en paralelo con 13 estrategias técnicas tradicionales (EMA, gap fade, RSI, Bollinger, VWAP, supertrend, etc). En Eolo v2 también corre otro engine de Claude llamado **OptionsBrain** que se ocupa del lado de opciones (calls/puts, debit spreads). **VOS NO OPERÁS OPCIONES** — vos operás solo las ACCIONES DEL UNDERLYING (shares). OptionsBrain es un engine separado, no tenés que coordinarte con él.

Tu operación es **independiente**: tomás decisiones basadas en el snapshot actual del mercado y opcionalmente considerás qué están diciendo las otras estrategias como pista, pero no tenés que validarlas.

FECHA/HORA: {mkt_time}

══════ TICKERS MONITOREADOS ══════
Universe: {ticks}

══════ SNAPSHOT DE PRECIOS ══════
{prices_text}

══════ SEÑALES RECIENTES DE LAS 13 ESTRATEGIAS (HOY) ══════
{signals_text}
Resumen: {buy_n} BUY | {sell_n} SELL

══════ POSICIONES ABIERTAS (SHARES) ══════
{positions_text}

══════ STATS DEL DÍA ══════
{stats_text}
Budget por trade: ${budget}

══════ INSTRUCCIONES ══════
Decidí UNA acción sobre SHARES (no opciones). Podés:
  • BUY un ticker que aún no tengas en posición.
  • SELL un ticker que SÍ tengas en posición (cerrarlo).
  • HOLD — no hacer nada este ciclo.

REGLAS CRÍTICAS (no las violes):
 1. NO abras una posición en un ticker que ya esté en tus posiciones abiertas.
 2. Solo podés cerrar (SELL) posiciones que figuren en la lista de "Posiciones abiertas".
 3. Si tenés >=3 posiciones abiertas, preferí HOLD o SELL en vez de otra BUY.
 4. Si el market está en los últimos 15 min antes del cierre (después de 15:45 ET), NO abras nuevas posiciones (solo cerrá las que haya).
 5. Sé cauto: HOLD es aceptable la mayoría de los ciclos. Solo actuá si hay una tesis clara.
 6. Tu confianza debe reflejar honestamente la claridad de la oportunidad (0.0 a 1.0).
 7. stop_loss_pct y take_profit_pct son porcentajes positivos del precio de entrada (ej: 1.5 = 1.5%).
 8. NO operés opciones — eso lo hace OptionsBrain. Vos solo shares del underlying.

RESPONDÉ ÚNICAMENTE CON UN JSON VÁLIDO, sin ningún texto fuera del JSON, con este formato exacto:
{{
  "ticker": "SPY",
  "signal": "BUY" | "SELL" | "HOLD",
  "price": number (precio actual del ticker al que te referís, 0 si HOLD global),
  "reasoning": "explicación concisa en 1-3 oraciones de tu tesis",
  "confidence": number 0.0-1.0,
  "stop_loss_pct": number (ej 1.5),
  "take_profit_pct": number (ej 3.0),
  "strategy_used": "nombre corto del approach que usaste (ej: 'momentum 5m breakout', 'mean reversion RSI', 'trend-follow 15m')"
}}

Si elegís HOLD global (no operar ningún ticker), usá ticker="NONE" y price=0.
"""
        return prompt

    # ── Llamada síncrona ─────────────────────────────

    def _call_claude(self, prompt: str) -> str:
        msg = self._client.messages.create(
            model       = self._model,
            max_tokens  = MAX_TOKENS,
            temperature = TEMPERATURE,
            messages    = [{"role": "user", "content": prompt}],
        )
        # El response.content es una lista de bloques; tomamos el primer text block.
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                return block.text
        # Fallback defensivo
        return str(msg.content[0].text) if msg.content else ""

    # ── Parser ───────────────────────────────────────

    def _parse_response(self, raw: str) -> dict:
        text = raw.strip()
        # Remover markdown fences si los puso
        if text.startswith("```"):
            lines = text.split("\n")
            text  = "\n".join(l for l in lines if not l.startswith("```"))

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Segundo intento: buscar el primer {...} balanceado
            start = text.find("{")
            end   = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end+1])
                except Exception:
                    raise ValueError(f"No se pudo parsear JSON: {text[:200]}")
            else:
                raise ValueError(f"No hay JSON en la respuesta: {text[:200]}")

        signal = str(data.get("signal", "HOLD")).upper().strip()
        if signal not in VALID_SIGNALS:
            signal = "HOLD"

        ticker = str(data.get("ticker", "NONE")).upper().strip()
        try:
            price = float(data.get("price", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0
        try:
            conf = float(data.get("confidence", 0) or 0)
            conf = max(0.0, min(1.0, conf))
        except (TypeError, ValueError):
            conf = 0.0
        try:
            sl = float(data.get("stop_loss_pct", 0) or 0)
        except (TypeError, ValueError):
            sl = 0.0
        try:
            tp = float(data.get("take_profit_pct", 0) or 0)
        except (TypeError, ValueError):
            tp = 0.0

        return {
            "ticker":          ticker,
            "signal":          signal,
            "price":           price,
            "reasoning":       str(data.get("reasoning", ""))[:500],
            "confidence":      conf,
            "stop_loss_pct":   sl,
            "take_profit_pct": tp,
            "strategy_used":   str(data.get("strategy_used", ""))[:80],
            "candle_time":     datetime.now(timezone.utc).isoformat(),
        }

    # ── Props / stats ────────────────────────────────

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def paper_mode(self) -> bool:
        return self._paper_mode

    @paper_mode.setter
    def paper_mode(self, v: bool):
        self._paper_mode = bool(v)

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, m: str):
        self._model = m
