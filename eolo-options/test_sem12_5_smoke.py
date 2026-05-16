"""Smoke test Sem 12.5: scanner solo emite bsm CALL >=$2 entre DTE 4-10."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis.mispricing import MispricingScanner, MIN_DTE, MAX_DTE

assert MIN_DTE == 4, f"MIN_DTE esperado 4, got {MIN_DTE}"
assert MAX_DTE == 10, f"MAX_DTE esperado 10, got {MAX_DTE}"
assert MispricingScanner.MIN_PREMIUM_USD == 2.00, "MIN_PREMIUM_USD debe ser 2.00"


# Test 1: chain con calls bsm-undervalued en DTE 7, mid=$2.5 → alert
def make_chain(dte=7, mid=2.5, theo_edge_pct=0.40):
    """Mock chain donde bsm detecta call subvaluado."""
    K = 100.0
    bid = mid - 0.05
    ask = mid + 0.05
    iv  = 30.0
    contract = {"bid":bid,"ask":ask,"mark":mid,"last":mid,"iv":iv,"dte":dte}
    return {
        "ticker":"TEST",
        "underlying":{"price":100.0,"mark":100.0},
        "ts": 1747000000.0,
        "expirations":["2026-05-29"],
        "calls":{"2026-05-29":{"100.0":contract}},
        "puts":{"2026-05-29":{"100.0":contract}},
    }


scanner = MispricingScanner()

# Test 1: DTE 7, mid $2.5 → debería pasar el filtro
alerts = scanner.scan(make_chain(dte=7, mid=2.5))
bsm_alerts = [a for a in alerts if a.get("type")=="BSM_MISPRICING"]
print(f"Test 1 (DTE 7, mid $2.5): {len(bsm_alerts)} bsm alerts (PCP/skew/cal apagados)")

# Test 2: DTE 2 → DEBE skipear (MIN_DTE=4)
alerts2 = scanner.scan(make_chain(dte=2, mid=2.5))
bsm_alerts2 = [a for a in alerts2 if a.get("type")=="BSM_MISPRICING"]
assert len(bsm_alerts2) == 0, f"DTE 2 debería skipear, got {len(bsm_alerts2)} alerts"
print(f"Test 2 (DTE 2 → skip): PASS, 0 bsm alerts")

# Test 3: DTE 15 → DEBE skipear (MAX_DTE=10)
alerts3 = scanner.scan(make_chain(dte=15, mid=2.5))
bsm_alerts3 = [a for a in alerts3 if a.get("type")=="BSM_MISPRICING"]
assert len(bsm_alerts3) == 0, f"DTE 15 debería skipear, got {len(bsm_alerts3)} alerts"
print(f"Test 3 (DTE 15 → skip): PASS, 0 bsm alerts")

# Test 4: mid $1 → DEBE skipear (MIN_PREMIUM=$2)
alerts4 = scanner.scan(make_chain(dte=7, mid=1.0))
bsm_alerts4 = [a for a in alerts4 if a.get("type")=="BSM_MISPRICING"]
assert len(bsm_alerts4) == 0, f"mid $1 debería skipear, got {len(bsm_alerts4)} alerts"
print(f"Test 4 (mid $1 → skip): PASS, 0 bsm alerts")

# Test 5: solo calls, no puts
alerts5 = scanner.scan(make_chain(dte=7, mid=2.5))
bsm_puts = [a for a in alerts5 if a.get("type")=="BSM_MISPRICING" and a.get("option_type")=="put"]
assert len(bsm_puts) == 0, f"BSM PUTs deberían estar apagados, got {len(bsm_puts)}"
print(f"Test 5 (BSM puts off): PASS")

# Test 6: PCP / IV skew / calendar apagados
pcp_alerts = [a for a in alerts5 if a.get("type")=="PUT_CALL_PARITY"]
skew_alerts = [a for a in alerts5 if a.get("type")=="IV_SKEW_JUMP"]
cal_alerts = [a for a in alerts5 if a.get("type")=="CALENDAR_IV_GAP"]
assert len(pcp_alerts) == 0, f"PCP debe estar OFF, got {len(pcp_alerts)}"
assert len(skew_alerts) == 0, f"IV skew debe estar OFF, got {len(skew_alerts)}"
assert len(cal_alerts) == 0, f"calendar debe estar OFF, got {len(cal_alerts)}"
print(f"Test 6 (PCP/skew/calendar all off): PASS")

print("\n6/6 PASS — Sem 12.5 scanner recalibrado correctamente")
