# ============================================================
#  EOLO v2 — Options Brain (Claude API Decision Engine)
#
#  Claude analiza en tiempo real:
#    1. Quotes del subyacente (WebSocket stream)
#    2. Cadena de opciones completa (cada 30s)
#    3. Superficie IV + skew + term structure
#    4. Alertas de mispricing detectadas
#    5. Señales de las 13 estrategias de Eolo v1
#
#  Y decide:
#    - Qué contrato comprar/vender
#    - Precio límite sugerido (entre mid y ask)
#    - Stop loss y take profit en % del premium
#    - Razón de la decisión (trazable)
#
#  Respuesta JSON estructurada:
#    {
#      "action":      "BUY" | "SELL" | "HOLD",
#      "option_type": "call" | "put",
#      "ticker":      "SOXL",
#      "expiration":  "2025-05-16",
#      "strike":      45.0,
#      "contracts":   1,
#      "limit_price": 2.35,
#      "stop_loss_pct": 50,
#      "take_profit_pct": 100,
#      "confidence":  "HIGH" | "MEDIUM" | "LOW",
#      "reason":      "...",
#      "mispricing":  true | false,
#      "mispricing_type": "PUT_CALL_PARITY" | null,
#    }
#
#  Uso:
#    brain = OptionsBrain()
#    decision = await brain.analyze(
#        ticker, quote, chain, surface, mispricing_alerts, signals
#    )
# ============================================================
import json
import asyncio
from datetime import datetime, timezone
from loguru import logger

try:
    import anthropic
except ImportError:
    anthropic = None

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ── Configuración ──────────────────────────────────────────
# Haiku 4.5 — ~10-15x más barato que Sonnet; calidad suficiente para decisiones
# sobre opciones dado que el contexto es tabular/estructurado. Override con env.
CLAUDE_MODEL      = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
MAX_TOKENS        = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "512"))
TEMPERATURE       = 0.1          # decisiones deterministas

# Máximo de alertas de mispricing a incluir en el prompt
MAX_MISPRICING    = 5
# Máximo de strikes en el prompt de la cadena
MAX_STRIKES       = 10


class OptionsBrain:
    """
    Motor de decisión basado en Claude API para operar opciones.
    Analiza datos de mercado en tiempo real y retorna órdenes concretas.
    """

    def __init__(self, api_key: str | None = None):
        if anthropic is None:
            raise ImportError("Instalá: pip install anthropic")

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            # Fallback a Google Secret Manager (mismo patrón que ClaudeBotEngine)
            key = self._load_api_key_from_secret_manager()
        if not key:
            raise ValueError(
                "ANTHROPIC_API_KEY no disponible ni en env ni en Secret Manager — "
                "configurá el secreto con: gcloud secrets create ANTHROPIC_API_KEY --data-file=-"
            )

        self._client = anthropic.Anthropic(api_key=key)
        self._call_count = 0
        # Defaults de SL/TP que se aplican cuando Claude no manda valores
        # propios. Se pisan desde eolo_v2_main.py al refrescar config (modal).
        self.default_stop_loss_pct   = 50.0
        self.default_take_profit_pct = 100.0

    def set_risk_defaults(self, stop_loss_pct: float, take_profit_pct: float):
        """Permite al orquestador propagar los valores del modal Config."""
        try:
            self.default_stop_loss_pct   = float(stop_loss_pct)
            self.default_take_profit_pct = float(take_profit_pct)
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _load_api_key_from_secret_manager(
        secret_name: str = "ANTHROPIC_API_KEY",
    ) -> str | None:
        """Lee ANTHROPIC_API_KEY desde Google Secret Manager.
        Devuelve None si no se puede leer (logueará el error, no crashea)."""
        try:
            from google.cloud import secretmanager
        except ImportError:
            return None
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        if not project_id:
            return None
        try:
            client = secretmanager.SecretManagerServiceClient()
            name   = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8").strip()
        except Exception as e:
            logger.warning(
                f"[OPTIONS_BRAIN] No pude leer {secret_name} de Secret Manager: {e}"
            )
            return None

    # ── Entrada principal ──────────────────────────────────

    async def analyze(
        self,
        ticker:             str,
        quote:              dict,           # quote en tiempo real del subyacente
        chain:              dict,           # cadena de opciones normalizada
        surface:            "IVSurface",    # superficie IV
        mispricing_alerts:  list[dict],     # alertas de mispricing
        open_positions:     list[dict] | None = None,  # posiciones abiertas actualmente
    ) -> dict:
        """
        Llama a Claude API y retorna una decisión de trading.

        Retorna:
            dict con la decisión (action, strike, expiration, etc.)
            o {"action": "HOLD", "reason": "..."}  si no hay oportunidad.
        """
        loop = asyncio.get_event_loop()
        prompt = self._build_prompt(
            ticker, quote, chain, surface,
            mispricing_alerts, open_positions
        )

        logger.info(f"[BRAIN] Consultando Claude para {ticker}...")
        self._call_count += 1

        # Llamada a Claude en threadpool (blocking)
        response = await loop.run_in_executor(
            None, self._call_claude, prompt
        )

        decision = self._parse_response(response, ticker)
        logger.info(
            f"[BRAIN] {ticker} → {decision.get('action')} "
            f"| {decision.get('option_type','')} "
            f"K={decision.get('strike','')} "
            f"| {decision.get('expiration','')} "
            f"| confidence={decision.get('confidence','')} "
            f"| {decision.get('reason','')[:80]}"
        )
        return decision

    # ── Construcción del prompt ────────────────────────────

    def _build_prompt(
        self,
        ticker, quote, chain, surface,
        mispricing_alerts, open_positions
    ) -> str:
        now_et = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M ET")

        # ── Sección 1: Quote del subyacente
        S = quote.get("last") or quote.get("mark") or quote.get("ask", 0)
        bid_u  = quote.get("bid", "n/a")
        ask_u  = quote.get("ask", "n/a")
        vol_u  = quote.get("volume", "n/a")
        open_u = quote.get("open", "n/a")
        high_u = quote.get("high", "n/a")
        low_u  = quote.get("low", "n/a")

        # ── Sección 2: IV Surface
        iv_summary = surface.to_summary() if surface else "IV Surface no disponible"
        atm_iv   = surface.atm_iv   if surface else None
        skew_idx = surface.skew_index if surface else None

        # ── Sección 3: Alertas de mispricing (top N)
        top_misprice = mispricing_alerts[:MAX_MISPRICING]
        if top_misprice:
            misprice_text = "\n".join(
                f"  [{a['severity']}] {a['type']} K={a.get('strike','')} "
                f"exp={a.get('expiration','')} edge=${a.get('edge',0):.3f} → {a['action']}\n"
                f"    {a['description']}"
                for a in top_misprice
            )
        else:
            misprice_text = "  Ninguna anomalía detectada."

        # ── Sección 4: Posiciones abiertas
        if open_positions:
            pos_lines = [
                f"  {p.get('ticker')} {p.get('option_type','').upper()} "
                f"K={p.get('strike')} exp={p.get('expiration')} "
                f"qty={p.get('contracts',1)} entry=${p.get('entry_price',0):.2f} "
                f"current=${p.get('current_price',0):.2f} "
                f"P&L={p.get('unrealized_pnl_pct',0):+.1f}%"
                for p in open_positions
            ]
            positions_text = "\n".join(pos_lines)
        else:
            positions_text = "  Sin posiciones abiertas."

        # ── Sección 6: Cadena de opciones (strikes cercanos ATM)
        chain_text = self._format_chain_for_prompt(chain, S)

        # ── Prompt completo ────────────────────────────────
        prompt = f"""Eres el motor de decisión automático de EOLO v2, un sistema de trading de opciones.

FECHA/HORA: {now_et}

══════ SUBYACENTE: {ticker} ══════
Precio actual : ${S:.2f}
Bid/Ask       : ${bid_u} / ${ask_u}
Open/High/Low : ${open_u} / ${high_u} / ${low_u}
Volumen       : {vol_u}

══════ VOLATILIDAD IMPLÍCITA ══════
{iv_summary}

══════ ANOMALÍAS DE PRECIO DETECTADAS ══════
{misprice_text}

══════ CADENA DE OPCIONES (strikes ATM ±20%) ══════
{chain_text}

══════ POSICIONES ABIERTAS ══════
{positions_text}

══════ INSTRUCCIONES DE DECISIÓN ══════
Analiza todos los datos anteriores y decide UNA acción para {ticker}.

REGLAS DE TRADING (CRÍTICAS — no las violes):
1. Solo instrumentos con Level 1-2 de opciones:
   - Long calls (BUY CALL)
   - Long puts (BUY PUT)
   - Covered calls (solo si ya hay posición larga en el subyacente — ignorar por ahora)
   - Cash-secured puts (si tienes capital para cubrir 100 acciones × strike)
   - Debit spreads (compra un contrato, vende otro más OTM del mismo tipo y exp)
2. Vencimientos preferidos: 14-30 DTE (2-4 semanas)
3. Strikes preferidos: delta 0.30-0.50 (cerca del ATM)
4. NO operar si el bid-ask spread supera el 25% del mid price
5. NO operar contratos con volumen < 50 o OI < 100
6. Máximo riesgo por operación: 2% del portafolio (asumí $10,000 capital)
7. Si hay una anomalía de mispricing HIGH con edge > $0.30, priorizarla
8. Stop loss: 50% del premium pagado; Take profit: 100% del premium

RESPONDE ÚNICAMENTE con un JSON válido con este formato exacto:
{{
  "action": "BUY" | "SELL_TO_CLOSE" | "HOLD",
  "option_type": "call" | "put" | null,
  "ticker": "{ticker}",
  "expiration": "YYYY-MM-DD" | null,
  "strike": number | null,
  "contracts": number,
  "limit_price": number | null,
  "stop_loss_pct": number,
  "take_profit_pct": number,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "mispricing": true | false,
  "mispricing_type": "PUT_CALL_PARITY" | "IV_SKEW_JUMP" | "BSM_MISPRICING" | "BUTTERFLY_ARBITRAGE" | "CALENDAR_IV_GAP" | null,
  "reason": "explicación en 1-2 oraciones de por qué esta decisión"
}}

Si no hay oportunidad clara, retorna {{"action": "HOLD", "reason": "...", "confidence": "LOW", ...}} con nulls en los campos opcionales.
No incluyas ningún texto fuera del JSON."""

        return prompt

    def _format_chain_for_prompt(self, chain: dict, S: float) -> str:
        """Formatea la cadena de opciones para incluir en el prompt.
        Se incluyen TODAS las expiraciones disponibles (weeklies + monthlies +
        quarterlies + LEAPS) para que Claude elija la óptima según su criterio
        y las reglas de DTE del prompt."""
        lines = []
        exps = chain.get("expirations", [])   # todas las expiraciones

        for exp in exps:
            calls = chain.get("calls", {}).get(exp, {})
            puts  = chain.get("puts",  {}).get(exp, {})

            # Strikes cercanos al ATM
            all_strikes = sorted(set(
                [float(k) for k in calls.keys()] +
                [float(k) for k in puts.keys()]
            ), key=lambda k: abs(k - S))[:MAX_STRIKES]
            all_strikes.sort()

            lines.append(f"\n{exp} — DTE={next(iter(calls.values()), {}).get('dte', '?')}:")
            lines.append(f"  {'Strike':>8} | {'C-Bid':>6} {'C-Ask':>6} {'C-IV':>6} {'C-Delta':>8} | {'P-Bid':>6} {'P-Ask':>6} {'P-IV':>6} {'P-Delta':>8}")
            lines.append("  " + "-" * 80)

            for k in all_strikes:
                k_str = str(k)
                c = calls.get(k_str, {})
                p = puts.get(k_str, {})

                atm_marker = " ←ATM" if abs(k - S) < (S * 0.02) else ""

                def fmt(v, decimals=2):
                    return f"{v:.{decimals}f}" if v is not None else "  n/a"

                c_bid   = fmt(c.get("bid"))
                c_ask   = fmt(c.get("ask"))
                c_iv    = fmt((c.get("iv") or 0), 1) + "%" if c.get("iv") else "  n/a"
                c_delta = fmt(c.get("delta"), 3)

                p_bid   = fmt(p.get("bid"))
                p_ask   = fmt(p.get("ask"))
                p_iv    = fmt((p.get("iv") or 0), 1) + "%" if p.get("iv") else "  n/a"
                p_delta = fmt(p.get("delta"), 3)

                lines.append(
                    f"  {k:>8.1f} | {c_bid:>6} {c_ask:>6} {c_iv:>6} {c_delta:>8} | "
                    f"{p_bid:>6} {p_ask:>6} {p_iv:>6} {p_delta:>8}{atm_marker}"
                )

        return "\n".join(lines) if lines else "  Cadena no disponible"

    # ── Llamada a Claude ───────────────────────────────────

    def _call_claude(self, prompt: str) -> str:
        """Llama a Claude API de forma sincrónica."""
        message = self._client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    # ── Parser de respuesta ────────────────────────────────

    def _parse_response(self, raw: str, ticker: str) -> dict:
        """
        Parsea la respuesta JSON de Claude.
        Retorna HOLD si hay error de parsing.
        """
        try:
            # Limpiar markdown fences si Claude los incluyó
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(
                    line for line in lines
                    if not line.startswith("```")
                )

            decision = json.loads(text)

            # Validar campos mínimos
            if "action" not in decision:
                raise ValueError("Falta campo 'action'")

            # Asegurar defaults
            decision.setdefault("ticker", ticker)
            decision.setdefault("contracts", 1)
            decision.setdefault("stop_loss_pct",   self.default_stop_loss_pct)
            decision.setdefault("take_profit_pct", self.default_take_profit_pct)
            decision.setdefault("confidence", "LOW")
            decision.setdefault("mispricing", False)
            decision.setdefault("mispricing_type", None)

            return decision

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"[BRAIN] Error parseando respuesta de Claude: {e}\nRaw: {raw[:200]}")
            return {
                "action":    "HOLD",
                "ticker":    ticker,
                "reason":    f"Error parseando respuesta: {e}",
                "confidence": "LOW",
                "mispricing": False,
            }

    # ── Stats ──────────────────────────────────────────────

    @property
    def call_count(self) -> int:
        """Número total de llamadas a Claude API."""
        return self._call_count
