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
from anthropic import Anthropic, APIError, APITimeoutError
from llm_engine.kb_loader import KBLoader
from llm_engine.market_snapshot import MarketSnapshot
from llm_engine.prompt_builder import build_prompts
from llm_engine.decision_parser import safe_decision_pipeline, Decision

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

    CONFIG = {
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
        "LLM_MODEL": os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929"),
        "LLM_MAX_TOKENS": int(os.getenv("LLM_MAX_TOKENS", "4096")),
        "LLM_TEMPERATURE": float(os.getenv("LLM_TEMPERATURE", "0.3")),
        "KB_PATH": os.getenv("KB_PATH", "/app/kb/EOLO_ThetaHarvest_v1.1.xlsx"),
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
        llm_latency_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[{request_id}] LLM response in {llm_latency_ms}ms")

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
    decision = safe_decision_pipeline(raw_output, snapshot)

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
        "kb_version": "v1.1",
    }
    return result


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
