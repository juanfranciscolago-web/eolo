"""Entry executor — wraps el call a /decide para entrada coordinada por fase.

Master Plan v2.1 sec 8.2-8.3.
"""
from typing import Optional


def evaluate_entry(ticker: str, snapshot: dict, llm_engine_url: str, auth_token: str) -> dict:
    """Call /decide on engine + return verdict for entry decision.

    Returns:
        {"verdict": "...", "confidence": int, "ok_to_execute": bool}
    """
    import json
    import urllib.request

    req = urllib.request.Request(
        f"{llm_engine_url}/decide",
        data=json.dumps(snapshot).encode(),
        headers={"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            decision = json.load(resp)
    except Exception as e:
        return {"verdict": "WAIT", "confidence": 0, "ok_to_execute": False, "error": str(e)[:200]}

    verdict = decision.get("verdict", "WAIT")
    confidence = decision.get("confidence", 0)
    ok = verdict in ("SELL_PUT", "SELL_CALL", "IRON_CONDOR_SEQUENTIAL") and confidence >= 6
    return {"verdict": verdict, "confidence": confidence, "ok_to_execute": ok, "decision": decision}
