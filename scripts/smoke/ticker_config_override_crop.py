#!/usr/bin/env python3
"""Sprint S3.3 smoke test: ticker_config editable (per-ticker override).

Pattern Alpha adaptado: override = dict per-ticker. 4 campos fluyen por cfg en
el scan (spread_width, delta_min_abs, delta_max_abs, max_dte); min_credit fluye
por _vix_credit_thresholds. Default=None → fallback a TICKER_CONFIG.

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
    TICKER_CONFIG,
    scan_theta_harvest,
    scan_theta_harvest_tranches,
    _vix_credit_thresholds,
)


def test_scan_signatures():
    """scan + tranches + _vix_credit_thresholds tienen kwarg ticker_cfg (default None)."""
    print("\n[1] Signatures con ticker_cfg...")
    for fn, name in [(scan_theta_harvest, "scan_theta_harvest"),
                     (scan_theta_harvest_tranches, "scan_theta_harvest_tranches"),
                     (_vix_credit_thresholds, "_vix_credit_thresholds")]:
        sig = inspect.signature(fn)
        assert "ticker_cfg" in sig.parameters, f"{name} sin kwarg ticker_cfg"
        default = sig.parameters["ticker_cfg"].default
        assert default is None, f"{name}.ticker_cfg default esperado None, got {default}"
        print(f"    [OK] {name}.ticker_cfg default=None")


def test_scan_uses_override():
    """scan_theta_harvest debe usar ticker_cfg or TICKER_CONFIG.get(ticker)."""
    print("\n[2] scan_theta_harvest usa cfg = ticker_cfg or TICKER_CONFIG...")
    with open(os.path.join(EOLO_CROP, "theta_harvest/theta_harvest_strategy.py")) as f:
        source = f.read()
    assert "cfg    = ticker_cfg or TICKER_CONFIG.get(ticker)" in source, \
        "scan_theta_harvest no usa ticker_cfg or TICKER_CONFIG.get"
    print("    [OK] cfg fallback presente")


def test_vix_credit_uses_local_cfg():
    """_vix_credit_thresholds debe usar _cfg = ticker_cfg or TICKER_CONFIG.get(ticker, {})."""
    print("\n[3] _vix_credit_thresholds usa _cfg...")
    with open(os.path.join(EOLO_CROP, "theta_harvest/theta_harvest_strategy.py")) as f:
        source = f.read()
    assert "_cfg = ticker_cfg or TICKER_CONFIG.get(ticker, {})" in source, \
        "_vix_credit_thresholds no usa _cfg local"
    # 3 lecturas reemplazadas (no debe quedar ninguna referencia directa a TICKER_CONFIG.get(ticker,{}).get('min_credit'))
    # contar las 3 referencias nuevas:
    assert source.count("_cfg.get(\"min_credit\", 0.40)") >= 3, \
        f"Esperaba ≥3 usos de _cfg.get('min_credit',...), got {source.count('_cfg.get(\"min_credit\", 0.40)')}"
    print("    [OK] _cfg.get('min_credit',...) en las 3 ramas")


def test_instance_var_init():
    """crop_main: self._ticker_config init desde TICKER_CONFIG."""
    print("\n[4] CropBotTheta._ticker_config init...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert "TICKER_CONFIG," in source, "TICKER_CONFIG no importado en crop_main"
    assert "self._ticker_config: dict = {t: dict(cfg) for t, cfg in TICKER_CONFIG.items()}" in source, \
        "Init self._ticker_config (copia profunda) no encontrado"
    print("    [OK] import + init copia profunda presentes")


def test_helper_dot_parse():
    """_apply tiene bloque parse dot para ticker_config con int_fields={'max_dte'}."""
    print("\n[5] Helper _apply bloque dot parse...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert 'prefix = "strategy_params.ticker_config."' in source, \
        "prefix de ticker_config no encontrado"
    assert 'int_fields = {"max_dte"}' in source, "int_fields {'max_dte'} no encontrado"
    assert 'tk, field = rest.split(".", 1)' in source, "split dot no encontrado"
    assert "self._ticker_config[tk][field] = int(val) if field in int_fields else float(val)" in source, \
        "asignación con int/float cast no encontrada"
    print("    [OK] bloque parse dot completo (max_dte=int, resto=float)")


def test_override_apply_logic():
    """Simular: 2 overrides → instance vars actualizadas con tipos correctos."""
    print("\n[6] Override apply logic simulation...")

    class MockBot:
        def __init__(self):
            self._ticker_config = {t: dict(cfg) for t, cfg in TICKER_CONFIG.items()}
            self._strategy_overrides = {}

    bot = MockBot()
    initial_spy_dmin   = bot._ticker_config["SPY"]["delta_min_abs"]
    initial_iwm_credit = bot._ticker_config["IWM"]["min_credit"]
    initial_qqq_full   = dict(bot._ticker_config["QQQ"])

    bot._strategy_overrides = {
        "strategy_params.ticker_config.SPY.spread_width": 10.0,
        "strategy_params.ticker_config.IWM.max_dte": 2,
    }

    # Apply (replica del bloque del helper)
    prefix = "strategy_params.ticker_config."
    int_fields = {"max_dte"}
    for k, val in bot._strategy_overrides.items():
        if not k.startswith(prefix):
            continue
        rest = k[len(prefix):]
        if "." not in rest:
            continue
        tk, field = rest.split(".", 1)
        if tk in bot._ticker_config and field in bot._ticker_config[tk]:
            try:
                bot._ticker_config[tk][field] = int(val) if field in int_fields else float(val)
            except (TypeError, ValueError):
                pass

    # Cambios esperados con tipos correctos
    assert bot._ticker_config["SPY"]["spread_width"] == 10.0, \
        f"SPY.spread_width: {bot._ticker_config['SPY']['spread_width']}"
    assert isinstance(bot._ticker_config["SPY"]["spread_width"], float), \
        f"SPY.spread_width debería ser float, got {type(bot._ticker_config['SPY']['spread_width']).__name__}"
    assert bot._ticker_config["IWM"]["max_dte"] == 2, \
        f"IWM.max_dte: {bot._ticker_config['IWM']['max_dte']}"
    assert isinstance(bot._ticker_config["IWM"]["max_dte"], int), \
        f"IWM.max_dte debería ser int, got {type(bot._ticker_config['IWM']['max_dte']).__name__}"
    # Lo que NO se tocó
    assert bot._ticker_config["SPY"]["delta_min_abs"] == initial_spy_dmin, \
        f"SPY.delta_min_abs cambió: {bot._ticker_config['SPY']['delta_min_abs']}"
    assert bot._ticker_config["IWM"]["min_credit"] == initial_iwm_credit, \
        f"IWM.min_credit cambió: {bot._ticker_config['IWM']['min_credit']}"
    assert bot._ticker_config["QQQ"] == initial_qqq_full, \
        f"QQQ cambió: {bot._ticker_config['QQQ']} vs {initial_qqq_full}"

    print(f"    [OK] SPY.spread_width: {TICKER_CONFIG['SPY']['spread_width']} → 10.0 (float)")
    print(f"    [OK] IWM.max_dte: {TICKER_CONFIG['IWM']['max_dte']} → 2 (int)")
    print(f"    [OK] SPY.delta_min_abs y IWM.min_credit untouched")
    print(f"    [OK] QQQ completo untouched")


def test_callsite_threading():
    """Callsite ENTRY pasa ticker_cfg = self._ticker_config.get(ticker)."""
    print("\n[7] Callsite ENTRY threading...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert "ticker_cfg            = self._ticker_config.get(ticker)" in source, \
        "Threading ticker_cfg no encontrado en _run_theta_harvest callsite"
    print("    [OK] ENTRY pasa ticker_cfg = self._ticker_config.get(ticker)")


def test_pivot_analysis_untouched():
    """No se modificó pivot_analysis.py."""
    print("\n[8] pivot_analysis.py untouched...")
    with open(os.path.join(EOLO_CROP, "theta_harvest/pivot_analysis.py")) as f:
        source = f.read()
    # No debería haber referencias a ticker_cfg en pivot_analysis
    assert "ticker_cfg" not in source, "ticker_cfg apareció en pivot_analysis.py (no debería)"
    print("    [OK] pivot_analysis.py no contiene ticker_cfg")


if __name__ == "__main__":
    print("=" * 60)
    print("Sprint S3.3 smoke test: ticker_config editable (per-ticker)")
    print("=" * 60)

    tests = [
        test_scan_signatures,
        test_scan_uses_override,
        test_vix_credit_uses_local_cfg,
        test_instance_var_init,
        test_helper_dot_parse,
        test_override_apply_logic,
        test_callsite_threading,
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
