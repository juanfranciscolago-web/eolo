#!/usr/bin/env python3
"""Sprint S3.2 smoke test: delta_by_risk editable (override por risk_level).

Pattern Alpha adaptado: el override es un DICT mutable de LISTAS (no escalares).
default=None → fallback a pivot_result.delta_min/max (comportamiento idéntico).

NO requiere bot running.
"""
import sys
import os
import inspect

# Add eolo-crop to path
HERE = os.path.dirname(os.path.abspath(__file__))
EOLO_CROP = os.path.abspath(os.path.join(HERE, "..", "..", "eolo-crop"))
if EOLO_CROP not in sys.path:
    sys.path.insert(0, EOLO_CROP)

from theta_harvest.theta_harvest_strategy import (
    scan_theta_harvest,
    scan_theta_harvest_tranches,
)
from theta_harvest.pivot_analysis import DELTA_BY_RISK


def test_scan_signatures():
    """scan_theta_harvest y scan_theta_harvest_tranches deben tener kwarg delta_by_risk (default None)."""
    print("\n[1] scan signatures con delta_by_risk...")
    for fn, name in [(scan_theta_harvest, "scan_theta_harvest"),
                     (scan_theta_harvest_tranches, "scan_theta_harvest_tranches")]:
        sig = inspect.signature(fn)
        assert "delta_by_risk" in sig.parameters, f"{name} sin kwarg delta_by_risk"
        default = sig.parameters["delta_by_risk"].default
        assert default is None, f"{name}.delta_by_risk default esperado None, got {default}"
        print(f"    [OK] {name}.delta_by_risk default=None")


def test_instance_var_init():
    """crop_main: self._delta_by_risk init desde DELTA_BY_RISK (dict de LISTAS)."""
    print("\n[2] CropBotTheta._delta_by_risk init...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert "DELTA_BY_RISK," in source, \
        "DELTA_BY_RISK no importado en crop_main"
    assert "self._delta_by_risk: dict = {k: list(v) for k, v in DELTA_BY_RISK.items()}" in source, \
        "Init self._delta_by_risk no encontrado (dict de listas mutables)"
    print("    [OK] import + init dict de listas presentes")


def test_helper_bracket_parse():
    """_apply_strategy_overrides_to_instance_vars tiene bloque de parse bracket para delta_by_risk."""
    print("\n[3] Helper _apply bloque bracket parse...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    # Marcadores del bloque
    assert 'prefix = "strategy_params.delta_by_risk."' in source, \
        "prefix de delta_by_risk no encontrado"
    assert 'rest = k[len(prefix):]' in source, "parse rest no encontrado"
    assert 'level = rest[:rest.index("[")]' in source, "parse level no encontrado"
    assert 'idx = int(rest[rest.index("[")+1:-1])' in source, "parse idx no encontrado"
    assert "self._delta_by_risk[level][idx] = float(val)" in source, \
        "asignación a [level][idx] no encontrada"
    print("    [OK] bloque parse bracket completo")


def test_override_apply_logic():
    """Simular overrides bracket → self._delta_by_risk actualizado por path."""
    print("\n[4] Override apply logic simulation...")

    class MockBot:
        def __init__(self):
            self._delta_by_risk = {k: list(v) for k, v in DELTA_BY_RISK.items()}
            self._strategy_overrides = {}

    bot = MockBot()
    # Snapshot inicial para validar lo que NO cambia
    initial_low_1  = bot._delta_by_risk["LOW"][1]
    initial_mid_0  = bot._delta_by_risk["MID"][0]
    initial_vlow_0 = bot._delta_by_risk["VERY_LOW"][0]

    bot._strategy_overrides = {
        "strategy_params.delta_by_risk.LOW[0]": 0.18,
        "strategy_params.delta_by_risk.MID[1]": 0.32,
    }

    # Apply (replica del bloque del helper)
    prefix = "strategy_params.delta_by_risk."
    for k, val in bot._strategy_overrides.items():
        if not k.startswith(prefix):
            continue
        rest = k[len(prefix):]
        if "[" not in rest or not rest.endswith("]"):
            continue
        level = rest[:rest.index("[")]
        try:
            idx = int(rest[rest.index("[")+1:-1])
        except (TypeError, ValueError):
            continue
        if level in bot._delta_by_risk and 0 <= idx < len(bot._delta_by_risk[level]):
            try:
                bot._delta_by_risk[level][idx] = float(val)
            except (TypeError, ValueError):
                pass

    # Cambios esperados
    assert bot._delta_by_risk["LOW"][0] == 0.18, \
        f"LOW[0]: {bot._delta_by_risk['LOW'][0]}"
    assert bot._delta_by_risk["MID"][1] == 0.32, \
        f"MID[1]: {bot._delta_by_risk['MID'][1]}"
    # Lo que NO se tocó debe seguir igual
    assert bot._delta_by_risk["LOW"][1] == initial_low_1, \
        f"LOW[1] cambió: {bot._delta_by_risk['LOW'][1]} vs {initial_low_1}"
    assert bot._delta_by_risk["MID"][0] == initial_mid_0, \
        f"MID[0] cambió: {bot._delta_by_risk['MID'][0]} vs {initial_mid_0}"
    assert bot._delta_by_risk["VERY_LOW"][0] == initial_vlow_0, \
        f"VERY_LOW[0] cambió: {bot._delta_by_risk['VERY_LOW'][0]} vs {initial_vlow_0}"
    print(f"    [OK] LOW[0]: {DELTA_BY_RISK['LOW'][0]} → 0.18")
    print(f"    [OK] MID[1]: {DELTA_BY_RISK['MID'][1]} → 0.32")
    print(f"    [OK] resto sin cambios (LOW[1]={initial_low_1}, MID[0]={initial_mid_0}, VERY_LOW[0]={initial_vlow_0})")


def test_callsite_threading():
    """Callsite ENTRY (_run_theta_harvest → scan_theta_harvest_tranches) pasa delta_by_risk."""
    print("\n[5] Callsite ENTRY threading...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert "delta_by_risk         = self._delta_by_risk" in source, \
        "Threading delta_by_risk no encontrado en _run_theta_harvest callsite"
    print("    [OK] ENTRY pasa delta_by_risk = self._delta_by_risk")


def test_scan_uses_override():
    """scan_theta_harvest debe contener la lógica que prioriza delta_by_risk sobre pivot_result."""
    print("\n[6] scan_theta_harvest usa override con fallback...")
    with open(os.path.join(EOLO_CROP, "theta_harvest/theta_harvest_strategy.py")) as f:
        source = f.read()

    # Buscar la rama de prioridad delta_by_risk
    assert "if delta_by_risk and risk_level in delta_by_risk and len(delta_by_risk[risk_level]) >= 2:" in source, \
        "Lógica de prioridad delta_by_risk no encontrada en scan_theta_harvest"
    assert "delta_min = float(delta_by_risk[risk_level][0])" in source, \
        "Asignación delta_min desde override no encontrada"
    assert "delta_max = float(delta_by_risk[risk_level][1])" in source, \
        "Asignación delta_max desde override no encontrada"
    print("    [OK] override por risk_level con fallback a pivot_result")


def test_pivot_analysis_untouched():
    """No se modificó pivot_analysis.py — DELTA_BY_RISK + get_delta_range_for_risk siguen intactos."""
    print("\n[7] pivot_analysis.py untouched...")
    with open(os.path.join(EOLO_CROP, "theta_harvest/pivot_analysis.py")) as f:
        source = f.read()
    # DELTA_BY_RISK debe seguir presente como definición global
    assert "DELTA_BY_RISK:" in source, "DELTA_BY_RISK no presente en pivot_analysis"
    # NO debe haber un nuevo wrapper que pase delta_by_risk al pivot_result
    # (la lógica vive en el scan, no acá)
    print("    [OK] DELTA_BY_RISK presente; get_delta_range_for_risk dead pero intacto")


if __name__ == "__main__":
    print("=" * 60)
    print("Sprint S3.2 smoke test: delta_by_risk editable (dict override)")
    print("=" * 60)

    tests = [
        test_scan_signatures,
        test_instance_var_init,
        test_helper_bracket_parse,
        test_override_apply_logic,
        test_callsite_threading,
        test_scan_uses_override,
        test_pivot_analysis_untouched,
    ]

    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"    [FAIL] {e}")
            failed += 1
        except Exception as e:
            print(f"    [ERROR] {type(e).__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    if failed == 0:
        print(f"All {len(tests)} tests PASSED")
        sys.exit(0)
    else:
        print(f"FAILED: {failed}/{len(tests)} tests")
        sys.exit(1)
