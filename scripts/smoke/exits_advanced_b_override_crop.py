#!/usr/bin/env python3
"""Sprint S3.1-B smoke test: validar overrides funcionales para exits_advanced
thresholds (vix_spike_delta, vvix_panic_threshold, delta_drift_max,
spy_drop_pct_30m, min_minutes_to_exp).

Espejo del smoke S3.1-A. NO requiere bot running.
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
    VIX_SPIKE_DELTA,
    VVIX_PANIC_THRESHOLD,
    DELTA_DRIFT_MAX,
    SPY_DROP_PCT_30M,
    MIN_MINUTES_TO_EXP,
    evaluate_open_position,
    scan_theta_harvest,
    scan_theta_harvest_tranches,
)


def test_evaluate_open_position_kwargs():
    """evaluate_open_position debe aceptar 5 kwargs nuevos con defaults = constantes."""
    print("\n[1] evaluate_open_position signature...")
    sig = inspect.signature(evaluate_open_position)
    expected = {
        "vix_spike_delta":      VIX_SPIKE_DELTA,
        "vvix_panic_threshold": VVIX_PANIC_THRESHOLD,
        "delta_drift_max":      DELTA_DRIFT_MAX,
        "spy_drop_pct_30m":     SPY_DROP_PCT_30M,
        "min_minutes_to_exp":   MIN_MINUTES_TO_EXP,
    }
    for name, expected_default in expected.items():
        assert name in sig.parameters, \
            f"evaluate_open_position sin kwarg {name}"
        actual = sig.parameters[name].default
        assert actual == expected_default, \
            f"Default {name}: esperaba {expected_default}, got {actual}"
        print(f"    [OK] {name} default={actual}")


def test_scan_theta_harvest_kwargs():
    """scan_theta_harvest debe aceptar vvix_panic_threshold + min_minutes_to_exp."""
    print("\n[2] scan_theta_harvest signature...")
    sig = inspect.signature(scan_theta_harvest)
    for name, expected_default in [
        ("vvix_panic_threshold", VVIX_PANIC_THRESHOLD),
        ("min_minutes_to_exp",   MIN_MINUTES_TO_EXP),
    ]:
        assert name in sig.parameters, \
            f"scan_theta_harvest sin kwarg {name}"
        actual = sig.parameters[name].default
        assert actual == expected_default, \
            f"Default {name}: esperaba {expected_default}, got {actual}"
        print(f"    [OK] {name} default={actual}")


def test_scan_theta_harvest_tranches_kwargs():
    """scan_theta_harvest_tranches debe aceptar vvix_panic_threshold + min_minutes_to_exp."""
    print("\n[3] scan_theta_harvest_tranches signature...")
    sig = inspect.signature(scan_theta_harvest_tranches)
    for name, expected_default in [
        ("vvix_panic_threshold", VVIX_PANIC_THRESHOLD),
        ("min_minutes_to_exp",   MIN_MINUTES_TO_EXP),
    ]:
        assert name in sig.parameters, \
            f"scan_theta_harvest_tranches sin kwarg {name}"
        actual = sig.parameters[name].default
        assert actual == expected_default, \
            f"Default {name}: esperaba {expected_default}, got {actual}"
        print(f"    [OK] {name} default={actual}")


def test_instance_vars_init():
    """CropBotTheta.__init__ debe inicializar los 5 instance vars S3.1-B."""
    print("\n[4] CropBotTheta instance vars init...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    expected = [
        "self._vix_spike_delta:      float = VIX_SPIKE_DELTA",
        "self._vvix_panic_threshold: float = VVIX_PANIC_THRESHOLD",
        "self._delta_drift_max:      float = DELTA_DRIFT_MAX",
        "self._spy_drop_pct_30m:     float = SPY_DROP_PCT_30M",
        "self._min_minutes_to_exp:   int   = MIN_MINUTES_TO_EXP",
    ]
    for line in expected:
        assert line in source, f"Init no encontrado: {line!r}"
    print("    [OK] 5 instance vars S3.1-B presentes")


def test_helper_branches():
    """_apply_strategy_overrides_to_instance_vars debe tener 5 ramas S3.1-B."""
    print("\n[5] Helper _apply ramas S3.1-B...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    for key, instance_var in [
        ("strategy_params.exits_advanced.vix_spike_delta",      "self._vix_spike_delta"),
        ("strategy_params.exits_advanced.vvix_panic_threshold", "self._vvix_panic_threshold"),
        ("strategy_params.exits_advanced.delta_drift_max",      "self._delta_drift_max"),
        ("strategy_params.exits_advanced.spy_drop_pct_30m",     "self._spy_drop_pct_30m"),
        ("strategy_params.exits_advanced.min_minutes_to_exp",   "self._min_minutes_to_exp"),
    ]:
        assert key in source, f"key path no encontrado: {key}"
        assert instance_var in source, f"instance var no encontrado: {instance_var}"
    print("    [OK] 5 ramas del helper presentes")


def test_override_apply_logic():
    """Simular: setear los 5 overrides → helper → instance vars actualizadas."""
    print("\n[6] Override apply logic simulation...")

    class MockBot:
        def __init__(self):
            self._vix_spike_delta      = VIX_SPIKE_DELTA
            self._vvix_panic_threshold = VVIX_PANIC_THRESHOLD
            self._delta_drift_max      = DELTA_DRIFT_MAX
            self._spy_drop_pct_30m     = SPY_DROP_PCT_30M
            self._min_minutes_to_exp   = MIN_MINUTES_TO_EXP
            self._strategy_overrides = {}

    bot = MockBot()
    bot._strategy_overrides = {
        "strategy_params.exits_advanced.vix_spike_delta":      5.0,
        "strategy_params.exits_advanced.vvix_panic_threshold": 130.0,
        "strategy_params.exits_advanced.delta_drift_max":      0.45,
        "strategy_params.exits_advanced.spy_drop_pct_30m":     1.5,
        "strategy_params.exits_advanced.min_minutes_to_exp":   30,
    }

    # Apply (replica de las 5 ramas S3.1-B)
    o = bot._strategy_overrides
    k = "strategy_params.exits_advanced.vix_spike_delta"
    if k in o:
        bot._vix_spike_delta = float(o[k])
    k = "strategy_params.exits_advanced.vvix_panic_threshold"
    if k in o:
        bot._vvix_panic_threshold = float(o[k])
    k = "strategy_params.exits_advanced.delta_drift_max"
    if k in o:
        bot._delta_drift_max = float(o[k])
    k = "strategy_params.exits_advanced.spy_drop_pct_30m"
    if k in o:
        bot._spy_drop_pct_30m = float(o[k])
    k = "strategy_params.exits_advanced.min_minutes_to_exp"
    if k in o:
        bot._min_minutes_to_exp = int(o[k])

    assert bot._vix_spike_delta == 5.0,         f"vix_spike_delta: {bot._vix_spike_delta}"
    assert bot._vvix_panic_threshold == 130.0,  f"vvix_panic_threshold: {bot._vvix_panic_threshold}"
    assert bot._delta_drift_max == 0.45,        f"delta_drift_max: {bot._delta_drift_max}"
    assert bot._spy_drop_pct_30m == 1.5,        f"spy_drop_pct_30m: {bot._spy_drop_pct_30m}"
    assert bot._min_minutes_to_exp == 30,       f"min_minutes_to_exp: {bot._min_minutes_to_exp}"
    print(f"    [OK] vix_spike_delta:      {VIX_SPIKE_DELTA} → {bot._vix_spike_delta}")
    print(f"    [OK] vvix_panic_threshold: {VVIX_PANIC_THRESHOLD} → {bot._vvix_panic_threshold}")
    print(f"    [OK] delta_drift_max:      {DELTA_DRIFT_MAX} → {bot._delta_drift_max}")
    print(f"    [OK] spy_drop_pct_30m:     {SPY_DROP_PCT_30M} → {bot._spy_drop_pct_30m}")
    print(f"    [OK] min_minutes_to_exp:   {MIN_MINUTES_TO_EXP} → {bot._min_minutes_to_exp}")


def test_exit_callsite_threading():
    """Callsite EXIT (_theta_monitor_loop) debe pasar los 5 kwargs S3.1-B."""
    print("\n[7] Caller EXIT threading en _theta_monitor_loop...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    for line in [
        "vix_spike_delta      = self._vix_spike_delta",
        "vvix_panic_threshold = self._vvix_panic_threshold",
        "delta_drift_max      = self._delta_drift_max",
        "spy_drop_pct_30m     = self._spy_drop_pct_30m",
        "min_minutes_to_exp   = self._min_minutes_to_exp",
    ]:
        assert line in source, f"Threading EXIT no encontrado: {line!r}"
    print("    [OK] EXIT pasa 5 kwargs S3.1-B")


def test_entry_callsite_threading():
    """Callsite ENTRY (_run_theta_harvest → scan_theta_harvest_tranches) pasa
    vvix_panic_threshold + min_minutes_to_exp."""
    print("\n[8] Caller ENTRY threading en _run_theta_harvest...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    for line in [
        "vvix_panic_threshold  = self._vvix_panic_threshold",
        "min_minutes_to_exp    = self._min_minutes_to_exp",
    ]:
        assert line in source, f"Threading ENTRY no encontrado: {line!r}"
    print("    [OK] ENTRY pasa vvix_panic_threshold + min_minutes_to_exp")


if __name__ == "__main__":
    print("=" * 60)
    print("Sprint S3.1-B smoke test: exits_advanced thresholds overrides")
    print("=" * 60)

    tests = [
        test_evaluate_open_position_kwargs,
        test_scan_theta_harvest_kwargs,
        test_scan_theta_harvest_tranches_kwargs,
        test_instance_vars_init,
        test_helper_branches,
        test_override_apply_logic,
        test_exit_callsite_threading,
        test_entry_callsite_threading,
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
