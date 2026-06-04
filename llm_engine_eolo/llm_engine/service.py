"""
LLM Engine Service - FastAPI app principal.

Endpoints:
- POST /decide       - Decision making endpoint
- GET  /health       - Health check
- GET  /kb_stats     - Stats del KB cargado
- GET  /docs         - Swagger UI (auto-generado)
"""
import os
import logging
import time
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from anthropic import Anthropic, APIError, APITimeoutError
from llm_engine.kb_loader import KBLoader
from llm_engine.market_snapshot import MarketSnapshot
from llm_engine.prompt_builder import build_prompts
from llm_engine.decision_parser import (
    safe_decision_pipeline, Decision,
    validate_rule_citations_from_lists, validate_decision_rule_citations,
)
from llm_engine.haiku_prefilter import (
    build_haiku_prompts, parse_pre_decision, PreDecision
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Global state
kb_loader: KBLoader = None
anthropic_client: Anthropic = None
CONFIG = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: cargar KB y validar config."""
    global kb_loader, anthropic_client, CONFIG

    # KB_PATH: auto-discover by glob (Finding G pattern del test) — sobrevive
    # futuros bump-version sin re-edit. Defensa adicional: si KB_PATH env var
    # está seteado pero apunta a archivo que NO existe (caso real deploy 00005
    # 00006 cuando deploy.sh tenía v1.2 hardcoded post-T2 bump a v1.3),
    # fallback al glob también.
    import glob as _glob
    import os.path as _osp
    import re as _re

    def _kb_version_key(path: str) -> tuple:
        m = _re.search(r"v(\d+)\.(\d+)", path)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    _kbs = sorted(_glob.glob("/app/kb/EOLO_ThetaHarvest_v*.xlsx"), key=_kb_version_key)
    _default_kb = _kbs[-1] if _kbs else "/app/kb/EOLO_ThetaHarvest_v1.3.xlsx"
    _env_kb = os.getenv("KB_PATH")
    if _env_kb and not _osp.exists(_env_kb):
        logger.warning(f"KB_PATH env var={_env_kb} no existe, fallback a {_default_kb}")
        _env_kb = None

    _resolved_kb = _env_kb or _default_kb
    _ver_m = _re.search(r"v(\d+\.\d+)", _resolved_kb)
    _resolved_kb_version = f"v{_ver_m.group(1)}" if _ver_m else "unknown"

    CONFIG = {
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
        "LLM_MODEL": os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929"),
        "HAIKU_MODEL": os.getenv("HAIKU_MODEL", "claude-haiku-4-5-20251001"),
        "LLM_MAX_TOKENS": int(os.getenv("LLM_MAX_TOKENS", "4096")),
        "LLM_TEMPERATURE": float(os.getenv("LLM_TEMPERATURE", "0.3")),
        "KB_PATH": _resolved_kb,
        "KB_VERSION": _resolved_kb_version,
        "PAPER_TRADING_ONLY": os.getenv("PAPER_TRADING_ONLY", "true").lower() == "true",
    }

    # Validate
    if not CONFIG["ANTHROPIC_API_KEY"]:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    if not CONFIG["PAPER_TRADING_ONLY"]:
        logger.warning("⚠️  PAPER_TRADING_ONLY is FALSE - live trading enabled!")

    # Load KB
    logger.info(f"Loading KB from {CONFIG['KB_PATH']}")
    kb_loader = KBLoader(CONFIG["KB_PATH"])
    logger.info(f"KB stats: {kb_loader.stats()}")

    # Init Anthropic client
    anthropic_client = Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])
    logger.info(f"Anthropic client ready - model: {CONFIG['LLM_MODEL']}")

    logger.info("LLM Engine Service READY")
    yield
    logger.info("LLM Engine Service shutting down")


app = FastAPI(
    title="EOLO Crop LLM Engine",
    description="Decision engine for Theta Harvest based on Juan's KB",
    version="0.1.0",
    lifespan=lifespan
)


@app.get("/health")
async def health():
    """Health check para Cloud Run."""
    return {
        "status": "healthy",
        "kb_loaded": kb_loader is not None,
        "paper_trading_only": CONFIG.get("PAPER_TRADING_ONLY"),
        "model": CONFIG.get("LLM_MODEL"),
    }


@app.get("/kb_stats")
async def kb_stats():
    """Stats del KB cargado."""
    if not kb_loader:
        raise HTTPException(503, "KB not loaded")
    return kb_loader.stats()


@app.post("/decide")
async def decide(snapshot: MarketSnapshot, request: Request) -> dict:
    """
    Endpoint principal: recibe MarketSnapshot, retorna Decision.

    Pipeline:
    1. Build prompts from KB + snapshot
    2. Call Anthropic API
    3. Parse + validate + safety rails
    4. Log everything
    5. Return decision
    """
    start_time = time.time()
    request_id = f"req_{int(start_time * 1000)}"

    if not CONFIG.get("PAPER_TRADING_ONLY"):
        raise HTTPException(403, "Live trading disabled - PAPER_TRADING_ONLY required")

    logger.info(f"[{request_id}] Decision request: {snapshot.ticker} @ ${snapshot.price}")

    # Build prompts
    try:
        system_prompt, user_prompt = build_prompts(kb_loader, snapshot)
    except Exception as e:
        logger.error(f"[{request_id}] Prompt build failed: {e}")
        raise HTTPException(500, f"Prompt build error: {str(e)}")

    # Call Claude
    try:
        response = anthropic_client.messages.create(
            model=CONFIG["LLM_MODEL"],
            max_tokens=CONFIG["LLM_MAX_TOKENS"],
            temperature=CONFIG["LLM_TEMPERATURE"],
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        raw_output = response.content[0].text
        # Sprint 21: usage stats para cost_estimate_usd real en LLMMetrics (bot CROP).
        # Defensive: si el SDK cambia o el response no trae usage, fallback a 0.
        _usage = getattr(response, "usage", None)
        input_tokens  = int(getattr(_usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(_usage, "output_tokens", 0) or 0)
        llm_latency_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"[{request_id}] LLM response in {llm_latency_ms}ms "
            f"(in={input_tokens} out={output_tokens})"
        )

    except APITimeoutError:
        logger.error(f"[{request_id}] Anthropic timeout")
        return _fallback_wait_decision("Anthropic API timeout", request_id, start_time)
    except APIError as e:
        logger.error(f"[{request_id}] Anthropic API error: {e}")
        return _fallback_wait_decision(f"API error: {str(e)[:100]}", request_id, start_time)
    except Exception as e:
        logger.error(f"[{request_id}] Unexpected LLM call error: {e}")
        return _fallback_wait_decision(f"LLM error: {str(e)[:100]}", request_id, start_time)

    # Parse + safety rails
    decision = safe_decision_pipeline(raw_output, snapshot, kb_loader=kb_loader)

    # Sprint ANTI-HALLUCINATION-FIX: post-parse rule_id existence check.
    # Appendea INVALID_RULE_CITATION_<id> a safety_overrides para audit (no
    # cambia verdict — la decisión puede estar bien razonada aunque cita fantasma).
    try:
        extra_overrides = validate_decision_rule_citations(decision, kb_loader)
        if extra_overrides:
            decision.safety_overrides = (decision.safety_overrides or []) + extra_overrides
    except Exception as _vc_e:
        logger.warning(f"[{request_id}] rule citation validation failed: {_vc_e}")

    # Log full decision
    total_latency_ms = int((time.time() - start_time) * 1000)
    log_entry = {
        "request_id": request_id,
        "timestamp": snapshot.timestamp,
        "ticker": snapshot.ticker,
        "price": snapshot.price,
        "vix": snapshot.vix_level,
        "vix_velocity_30m": snapshot.vix_velocity_30m_pct,
        "decision": decision.model_dump(),
        "raw_llm_output": raw_output[:500],
        "llm_latency_ms": llm_latency_ms,
        "total_latency_ms": total_latency_ms,
        "model": CONFIG["LLM_MODEL"],
    }
    logger.info(f"[{request_id}] DECISION_LOG: {json.dumps(log_entry, default=str)}")

    # Return enriched response
    result = decision.model_dump()
    result["meta"] = {
        "request_id": request_id,
        "latency_ms": total_latency_ms,
        "model": CONFIG["LLM_MODEL"],
        "kb_version": CONFIG.get("KB_VERSION", "unknown"),
        # Sprint 21: tokens consumidos en esta llamada. El bot CROP
        # (crop_main.py:1346-1353) ya los lee para LLMMetrics.record_call().
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
    }
    return result


@app.post("/pre_decide")
async def pre_decide(snapshot: MarketSnapshot, request: Request) -> dict:
    """
    Pre-filter con Haiku 4.5. Decide si vale la pena llamar a /decide (Sonnet).

    Output:
        {
          "should_call_full": bool,
          "reason": str,
          "haiku_confidence": int,
          "meta": {request_id, latency_ms, model}
        }

    Fallback policy: cualquier error (prompt build, API, parse) -> should_call_full=True
    (mejor pasar a Sonnet que perder oportunidad).
    """
    start_time = time.time()
    request_id = f"pre_{int(start_time * 1000)}"
    logger.info(f"[{request_id}] Pre-decide: {snapshot.ticker} @ ${snapshot.price}")

    try:
        system_prompt, user_prompt = build_haiku_prompts(kb_loader, snapshot)
    except Exception as e:
        logger.error(f"[{request_id}] Haiku prompt build failed: {e}")
        return {
            "should_call_full": True,
            "reason": f"haiku_prompt_build_error: {str(e)[:80]}",
            "haiku_confidence": 0,
            "meta": {"request_id": request_id, "fallback": True},
        }

    try:
        response = anthropic_client.messages.create(
            model=CONFIG["HAIKU_MODEL"],
            max_tokens=512,
            temperature=0.2,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        raw_output = response.content[0].text
        # Sprint 21: capturar usage del pre-filter Haiku también
        # (LLMMetrics tracks Haiku skips/passes; cost real requiere ambos).
        _usage = getattr(response, "usage", None)
        input_tokens  = int(getattr(_usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(_usage, "output_tokens", 0) or 0)
        latency_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"[{request_id}] Haiku response in {latency_ms}ms "
            f"(in={input_tokens} out={output_tokens})"
        )

    except (APITimeoutError, APIError) as e:
        logger.error(f"[{request_id}] Haiku API error: {e}")
        return {
            "should_call_full": True,
            "reason": f"haiku_api_error: {str(e)[:80]}",
            "haiku_confidence": 0,
            "meta": {"request_id": request_id, "fallback": True},
        }

    pre_decision = parse_pre_decision(raw_output)
    total_latency_ms = int((time.time() - start_time) * 1000)

    log_entry = {
        "request_id": request_id,
        "ticker": snapshot.ticker,
        "should_call_full": pre_decision.should_call_full,
        "reason": pre_decision.reason,
        "haiku_confidence": pre_decision.haiku_confidence,
        "latency_ms": total_latency_ms,
        "model": CONFIG["HAIKU_MODEL"],
    }
    logger.info(f"[{request_id}] PRE_DECISION_LOG: {json.dumps(log_entry)}")

    result = pre_decision.model_dump()
    result["meta"] = {
        "request_id": request_id,
        "latency_ms": total_latency_ms,
        "model": CONFIG["HAIKU_MODEL"],
        # Sprint 21: tokens del pre-filter Haiku. El bot CROP
        # contabiliza cost de ambos modelos (Haiku skip + Sonnet consult).
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
    }
    return result


# ===========================================================================
# Sprint T11/F5.B (Master Plan v2.1 sec 9.3): /juan/suggest endpoint
# ===========================================================================
class JuanSuggestionRequest(BaseModel):
    snapshot: MarketSnapshot
    suggestion_type: str = Field(pattern="^(ENTRY|EXIT|SIZE_DEBATE|MANUAL_TRADE_LOG)$")
    proposal: dict
    reasoning: str


@app.post("/juan/suggest")
async def juan_suggest(req: JuanSuggestionRequest) -> dict:
    """Evaluate Juan's proposal with dedicated LLM call.

    Sprint T11/F5.B. Devuelve JuanSuggestionResponse per Master Plan sec 9.3.
    """
    if not kb_loader:
        raise HTTPException(503, "KB not loaded")

    request_id = f"sugg_{int(time.time() * 1000)}"

    try:
        from llm_engine.prompt_builder import build_juan_suggestion_prompt
        similar_cases = kb_loader.balanced_get_similar_cases(req.snapshot, top_k=3)
        system_prompt, user_prompt = build_juan_suggestion_prompt(
            req.snapshot, req.suggestion_type, req.proposal, req.reasoning,
            similar_cases=similar_cases,
        )

        response = anthropic_client.messages.create(
            model=CONFIG["LLM_MODEL"],
            max_tokens=CONFIG["LLM_MAX_TOKENS"],
            temperature=CONFIG["LLM_TEMPERATURE"],
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_output = response.content[0].text

        try:
            cleaned = raw_output.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
                cleaned = cleaned.strip()
            parsed = json.loads(cleaned)
        except Exception as e:
            logger.warning(f"[{request_id}] failed to parse suggestion output: {e}")
            parsed = {
                "llm_verdict": "PARTIAL_AGREE",
                "confidence_in_juans_call": 5,
                "rules_supporting_juan": [],
                "rules_questioning_juan": [],
                "alternative_proposal": None,
                "final_recommendation": "DEFER",
                "reasoning": f"Output parse failed: {str(e)[:200]}",
            }

        # Sprint ANTI-HALLUCINATION-FIX: post-parse rule_id existence check.
        invalid_citations: list = []
        try:
            invalid_citations = validate_rule_citations_from_lists(
                rules_supporting=parsed.get("rules_supporting_juan", []) or [],
                rules_questioning=parsed.get("rules_questioning_juan", []) or [],
                kb_loader=kb_loader,
            )
            if invalid_citations:
                logger.warning(f"[{request_id}] juan_suggest hallucinated citations: {invalid_citations}")
        except Exception as _vc_e:
            logger.warning(f"[{request_id}] citation validation failed: {_vc_e}")

        meta: dict = {
            "request_id":    request_id,
            "input_tokens":  response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "model":         CONFIG["LLM_MODEL"],
        }
        if invalid_citations:
            meta["invalid_citations"] = invalid_citations
        return {
            **parsed,
            "_meta": meta,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{request_id}] juan_suggest failed: {e}")
        raise HTTPException(500, f"juan_suggest failed: {e}")


# ===========================================================================
# Sprint T11/Sprint 10 (Master Plan v2.1 sec 11.4): /feedback/chat endpoint
# ===========================================================================
class FeedbackChatRequest(BaseModel):
    snapshot_context: dict = Field(default_factory=dict)
    session_messages: list = Field(default_factory=list)
    journal: dict = Field(default_factory=dict)


@app.post("/feedback/chat")
async def feedback_chat(req: FeedbackChatRequest) -> dict:
    """Process feedback chat turn with dedicated LLM call.

    Sprint T11/Sprint 10. Returns response_text + artifacts_proposed.
    """
    request_id = f"fb_{int(time.time() * 1000)}"

    try:
        from llm_engine.prompt_builder import build_feedback_chat_prompt
        system_prompt, user_prompt = build_feedback_chat_prompt(
            req.snapshot_context, req.session_messages, req.journal,
        )

        response = anthropic_client.messages.create(
            model=CONFIG["LLM_MODEL"],
            max_tokens=CONFIG["LLM_MAX_TOKENS"],
            temperature=0.4,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_output = response.content[0].text

        try:
            cleaned = raw_output.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
                cleaned = cleaned.strip()
            parsed = json.loads(cleaned)
        except Exception as e:
            logger.warning(f"[{request_id}] feedback output parse failed: {e}")
            parsed = {
                "response_text": raw_output[:2000],
                "artifacts_proposed": [],
                "session_should_close": False,
            }

        return {
            **parsed,
            "_meta": {
                "request_id": request_id,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "model": CONFIG["LLM_MODEL"],
            },
        }
    except Exception as e:
        logger.error(f"[{request_id}] feedback_chat failed: {e}")
        raise HTTPException(500, f"feedback_chat failed: {e}")


def _fallback_wait_decision(reason: str, request_id: str, start_time: float) -> dict:
    """Fallback seguro cuando algo falla. SIEMPRE retorna WAIT."""
    return {
        "verdict": "WAIT",
        "confidence": 0,
        "strikes": {"put_strike": None, "call_strike": None},
        "deltas": {"put_delta": None, "call_delta": None},
        "dte_target": 0,
        "main_reason": reason,
        "tacit_rules_applied": [],
        "abort_triggers": [],
        "profit_target_pct": 50,
        "stop_loss_conditions": [],
        "similar_case_used": None,
        "warnings": [reason],
        "safety_overrides": ["FALLBACK_ACTIVATED"],
        "meta": {
            "request_id": request_id,
            "latency_ms": int((time.time() - start_time) * 1000),
            "fallback": True,
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
