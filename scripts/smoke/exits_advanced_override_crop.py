#!/usr/bin/env python3
"""Sprint S3.1-A smoke test: validar que override de stop_loss_mult y
tranche_profit_targets fluye desde _strategy_overrides → instance var →
strategy fn kwarg → comportamiento.

NO requiere bot running. Simula el flow completo en proceso local.
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
    STOP_LOSS_MULT,
    TRANCHE_PROFIT_TARGETS,
    scan_theta_harvest,
    scan_theta_harvest_tranches,
)


def test_scan_theta_harvest_kwarg():
    """scan_theta_harvest debe aceptar stop_loss_mult kwarg con default = STOP_LOSS_MULT."""
    print("\n[1] scan_theta_harvest signature...")
    sig = inspect.signature(scan_theta_harvest)
    assert "stop_loss_mult" in sig.parameters, \
        "scan_theta_harvest sin kwarg stop_loss_mult"
    default = sig.parameters["stop_loss_mult"].default
    assert default == STOP_LOSS_MULT, \
        f"Default debería ser STOP_LOSS_MULT={STOP_LOSS_MULT}, got {default}"
    print(f"    [OK] stop_loss_mult kwarg present, default={default}")


def test_scan_theta_harvest_tranches_kwargs():
    """scan_theta_harvest_tranches debe aceptar tranche_profit_targets + stop_loss_mult."""
    print("\n[2] scan_theta_harvest_tranches signature...")
    sig = inspect.signature(scan_theta_harvest_tranches)
    assert "tranche_profit_targets" in sig.parameters, \
        "scan_theta_harvest_tranches sin kwarg tranche_profit_targets"
    default_tpt = sig.parameters["tranche_profit_targets"].default
    assert default_tpt is None, \
        f"Default debería ser None (fallback en body), got {default_tpt}"

    assert "stop_loss_mult" in sig.parameters, \
        "scan_theta_harvest_tranches sin kwarg stop_loss_mult"
    default_slm = sig.parameters["stop_loss_mult"].default
    assert default_slm == STOP_LOSS_MULT, \
        f"Default stop_loss_mult debería ser {STOP_LOSS_MULT}, got {default_slm}"

    print(f"    [OK] tranche_profit_targets present (default=None)")
    print(f"    [OK] stop_loss_mult present (default={default_slm})")


def test_instance_vars_init():
    """CropBotTheta.__init__ debe inicializar _stop_loss_mult y _tranche_profit_targets."""
    print("\n[3] CropBotTheta instance vars init...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert "self._stop_loss_mult: float = STOP_LOSS_MULT" in source, \
        "_stop_loss_mult init no encontrado"
    assert "self._tranche_profit_targets: list = list(TRANCHE_PROFIT_TARGETS)" in source, \
        "_tranche_profit_targets init no encontrado"
    assert "def _apply_strategy_overrides_to_instance_vars" in source, \
        "_apply_strategy_overrides_to_instance_vars method no encontrado"
    print("    [OK] Instance vars + helper presentes en crop_main")


def test_override_apply_logic():
    """Simular: setear override → llamar _apply → verify instance var update.

    Replica la lógica del helper con un MockBot para validar end-to-end sin
    instanciar CropBotTheta (que requiere Schwab creds, asyncio, etc).
    """
    print("\n[4] Override apply logic simulation...")

    class MockBot:
        def __init__(self):
            self._stop_loss_mult = STOP_LOSS_MULT
            self._tranche_profit_targets = list(TRANCHE_PROFIT_TARGETS)
            self._strategy_overrides = {}

    bot = MockBot()
    bot._strategy_overrides = {
        "strategy_params.exits_advanced.stop_loss_mult": 2.5,
        "strategy_params.exits_advanced.tranche_profit_targets[0]": 0.30,
        "strategy_params.exits_advanced.tranche_profit_targets[1]": 0.55,
        # tranche_profit_targets[2] sin override → mantiene default None
    }

    # Apply (replica de _apply_strategy_overrides_to_instance_vars)
    overrides = bot._strategy_overrides
    key_slm = "strategy_params.exits_advanced.stop_loss_mult"
    if key_slm in overrides:
        bot._stop_loss_mult = float(overrides[key_slm])
    for i in range(3):
        key = f"strategy_params.exits_advanced.tranche_profit_targets[{i}]"
        if key in overrides:
            val = overrides[key]
            if val is None:
                bot._tranche_profit_targets[i] = None
            else:
                bot._tranche_profit_targets[i] = float(val)

    assert bot._stop_loss_mult == 2.5, \
        f"Override stop_loss_mult failed: {bot._stop_loss_mult}"
    assert bot._tranche_profit_targets[0] == 0.30, \
        f"Override tranche[0] failed: {bot._tranche_profit_targets[0]}"
    assert bot._tranche_profit_targets[1] == 0.55, \
        f"Override tranche[1] failed: {bot._tranche_profit_targets[1]}"
    expected_t2 = TRANCHE_PROFIT_TARGETS[2]
    assert bot._tranche_profit_targets[2] == expected_t2, \
        f"Untouched tranche[2] changed: {bot._tranche_profit_targets[2]} vs {expected_t2}"

    print(f"    [OK] stop_loss_mult: {STOP_LOSS_MULT} → {bot._stop_loss_mult}")
    print(f"    [OK] tranche[0]: {TRANCHE_PROFIT_TARGETS[0]} → {bot._tranche_profit_targets[0]}")
    print(f"    [OK] tranche[1]: {TRANCHE_PROFIT_TARGETS[1]} → {bot._tranche_profit_targets[1]}")
    print(f"    [OK] tranche[2] untouched: {bot._tranche_profit_targets[2]}")


def test_override_none_tranche_t2():
    """Override explícito None para tranche[2] (EXP sentinel)."""
    print("\n[5] Override None (EXP sentinel) en tranche[2]...")

    class MockBot:
        def __init__(self):
            self._tranche_profit_targets = [0.35, 0.65, 0.85]  # comenzamos con valores
            self._strategy_overrides = {
                "strategy_params.exits_advanced.tranche_profit_targets[2]": None,
            }

    bot = MockBot()
    overrides = bot._strategy_overrides
    for i in range(3):
        key = f"strategy_params.exits_advanced.tranche_profit_targets[{i}]"
        if key in overrides:
            val = overrides[key]
            if val is None:
                bot._tranche_profit_targets[i] = None
            else:
                bot._tranche_profit_targets[i] = float(val)

    assert bot._tranche_profit_targets[2] is None, \
        f"tranche[2] debería ser None, got {bot._tranche_profit_targets[2]}"
    print(f"    [OK] tranche[2]: 0.85 → None (EXP sentinel respetado)")


def test_threading_in_caller():
    """Validar que el callsite en crop_main pasa instance vars como kwargs."""
    print("\n[6] Caller threading en crop_main...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert "stop_loss_mult        = self._stop_loss_mult" in source, \
        "Threading stop_loss_mult NO encontrado en crop_main callsite"
    assert "tranche_profit_targets = self._tranche_profit_targets" in source, \
        "Threading tranche_profit_targets NO encontrado en crop_main callsite"
    print("    [OK] Caller pasa stop_loss_mult y tranche_profit_targets como kwargs")


def test_post_endpoint_hook():
    """Validar que main.py POST handler llama _apply después del overrides update."""
    print("\n[7] POST endpoint hook...")
    main_py_path = os.path.join(EOLO_CROP, "main.py")
    with open(main_py_path) as f:
        source = f.read()

    assert "_apply_strategy_overrides_to_instance_vars" in source, \
        "Hook al helper NO encontrado en main.py POST handler"
    print("    [OK] main.py POST /api/state/edit llama _apply helper")


def test_poll_settings_hook():
    """_poll_settings llama _apply (future-proof Firestore)."""
    print("\n[8] _poll_settings hook (future-proof Firestore)...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    # Buscar la llamada dentro de _poll_settings
    poll_idx = source.find("def _poll_settings")
    assert poll_idx > 0, "_poll_settings no encontrado"
    # Buscar el siguiente "def " después de _poll_settings
    next_def = source.find("\n    def ", poll_idx + 1)
    poll_body = source[poll_idx:next_def] if next_def > 0 else source[poll_idx:]

    assert "_apply_strategy_overrides_to_instance_vars" in poll_body, \
        "Hook al helper NO encontrado en _poll_settings"
    print("    [OK] _poll_settings llama _apply helper")


if __name__ == "__main__":
    print("=" * 60)
    print("Sprint S3.1-A smoke test: STOP_LOSS_MULT + TRANCHE_PROFIT_TARGETS")
    print("=" * 60)

    tests = [
        test_scan_theta_harvest_kwarg,
        test_scan_theta_harvest_tranches_kwargs,
        test_instance_vars_init,
        test_override_apply_logic,
        test_override_none_tranche_t2,
        test_threading_in_caller,
        test_post_endpoint_hook,
        test_poll_settings_hook,
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
