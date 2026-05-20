#!/usr/bin/env python3
"""Sprint S3.1-C smoke test: VIX velocity thresholds + window editables.

Cierra Exits Advanced (Pattern Alpha completo en 3 partes). Verifica que
override de vix_velocity_threshold_up_pct, vix_velocity_threshold_down_pct
y vix_velocity_window_seconds fluyen al loop (con recálculo del buffer maxlen).

NO requiere bot running.
"""
import sys
import os
from collections import deque

# Add eolo-crop to path
HERE = os.path.dirname(os.path.abspath(__file__))
EOLO_CROP = os.path.abspath(os.path.join(HERE, "..", "..", "eolo-crop"))
if EOLO_CROP not in sys.path:
    sys.path.insert(0, EOLO_CROP)

from theta_harvest.theta_harvest_strategy import (
    VIX_VELOCITY_THRESHOLD_UP_PCT,
    VIX_VELOCITY_THRESHOLD_DOWN_PCT,
    VIX_VELOCITY_WINDOW_SECONDS,
)


def test_constants_present():
    """3 constantes definidas en theta_harvest_strategy."""
    print("\n[1] Constantes VIX_VELOCITY_* en theta_harvest_strategy...")
    assert VIX_VELOCITY_THRESHOLD_UP_PCT == 0.03, \
        f"VIX_VELOCITY_THRESHOLD_UP_PCT esperaba 0.03, got {VIX_VELOCITY_THRESHOLD_UP_PCT}"
    assert VIX_VELOCITY_THRESHOLD_DOWN_PCT == -0.03, \
        f"VIX_VELOCITY_THRESHOLD_DOWN_PCT esperaba -0.03, got {VIX_VELOCITY_THRESHOLD_DOWN_PCT}"
    assert VIX_VELOCITY_WINDOW_SECONDS == 120, \
        f"VIX_VELOCITY_WINDOW_SECONDS esperaba 120, got {VIX_VELOCITY_WINDOW_SECONDS}"
    print(f"    [OK] UP_PCT={VIX_VELOCITY_THRESHOLD_UP_PCT}, DOWN_PCT={VIX_VELOCITY_THRESHOLD_DOWN_PCT}, WINDOW={VIX_VELOCITY_WINDOW_SECONDS}")


def test_instance_vars_init():
    """crop_main source tiene 3 instance vars + _vix_velocity_samples derivado + buffer dinámico."""
    print("\n[2] CropBotTheta instance vars S3.1-C...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    expected = [
        "self._vix_velocity_threshold_up_pct:   float = VIX_VELOCITY_THRESHOLD_UP_PCT",
        "self._vix_velocity_threshold_down_pct: float = VIX_VELOCITY_THRESHOLD_DOWN_PCT",
        "self._vix_velocity_window_seconds:     int   = VIX_VELOCITY_WINDOW_SECONDS",
        "self._vix_velocity_samples: int = max(2, round(self._vix_velocity_window_seconds / 30) + 1)",
        "self._vix_velocity_buffer: deque = deque(maxlen=self._vix_velocity_samples)",
    ]
    for line in expected:
        assert line in source, f"Init no encontrado: {line!r}"
    print("    [OK] 3 vars + samples derivado + buffer dinámico presentes")


def test_helper_branches():
    """_apply_strategy_overrides_to_instance_vars tiene 3 ramas vix_velocity_*."""
    print("\n[3] Helper _apply ramas S3.1-C...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    for key in [
        "strategy_params.exits_advanced.vix_velocity_threshold_up_pct",
        "strategy_params.exits_advanced.vix_velocity_threshold_down_pct",
        "strategy_params.exits_advanced.vix_velocity_window_seconds",
    ]:
        assert key in source, f"key path no encontrado: {key}"

    # window_seconds rama debe recrear el deque
    assert "deque(self._vix_velocity_buffer, maxlen=new_samples)" in source, \
        "Recreación del deque con maxlen nuevo no encontrada"
    print("    [OK] 3 ramas + recreación dinámica del deque")


def test_override_apply_logic():
    """Simular: 3 overrides → instance vars actualizadas + buffer maxlen recalculado.

    Para window_seconds=300, samples = max(2, round(300/30)+1) = 11.
    """
    print("\n[4] Override apply logic simulation...")

    class MockBot:
        def __init__(self):
            self._vix_velocity_threshold_up_pct   = VIX_VELOCITY_THRESHOLD_UP_PCT
            self._vix_velocity_threshold_down_pct = VIX_VELOCITY_THRESHOLD_DOWN_PCT
            self._vix_velocity_window_seconds     = VIX_VELOCITY_WINDOW_SECONDS
            self._vix_velocity_samples            = max(2, round(VIX_VELOCITY_WINDOW_SECONDS / 30) + 1)
            self._vix_velocity_buffer             = deque(maxlen=self._vix_velocity_samples)
            self._strategy_overrides              = {}

    bot = MockBot()
    # Llenar el buffer con valores para testear preservación
    bot._vix_velocity_buffer.extend([14.0, 14.5, 15.0, 15.2, 15.5])
    assert bot._vix_velocity_buffer.maxlen == 5, \
        f"Inicial maxlen=5 esperado, got {bot._vix_velocity_buffer.maxlen}"

    bot._strategy_overrides = {
        "strategy_params.exits_advanced.vix_velocity_threshold_up_pct":   0.05,
        "strategy_params.exits_advanced.vix_velocity_threshold_down_pct": -0.04,
        "strategy_params.exits_advanced.vix_velocity_window_seconds":     300,
    }

    # Apply (replica de las 3 ramas S3.1-C)
    o = bot._strategy_overrides
    k = "strategy_params.exits_advanced.vix_velocity_threshold_up_pct"
    if k in o:
        bot._vix_velocity_threshold_up_pct = float(o[k])
    k = "strategy_params.exits_advanced.vix_velocity_threshold_down_pct"
    if k in o:
        bot._vix_velocity_threshold_down_pct = float(o[k])
    k = "strategy_params.exits_advanced.vix_velocity_window_seconds"
    if k in o:
        new_secs = int(o[k])
        bot._vix_velocity_window_seconds = new_secs
        new_samples = max(2, round(new_secs / 30) + 1)
        if new_samples != bot._vix_velocity_samples:
            bot._vix_velocity_samples = new_samples
            bot._vix_velocity_buffer = deque(bot._vix_velocity_buffer, maxlen=new_samples)

    assert bot._vix_velocity_threshold_up_pct == 0.05, \
        f"up_pct: {bot._vix_velocity_threshold_up_pct}"
    assert bot._vix_velocity_threshold_down_pct == -0.04, \
        f"down_pct: {bot._vix_velocity_threshold_down_pct}"
    assert bot._vix_velocity_window_seconds == 300, \
        f"window_seconds: {bot._vix_velocity_window_seconds}"
    # samples para 300s: round(300/30)+1 = 10+1 = 11
    assert bot._vix_velocity_samples == 11, \
        f"samples esperado 11, got {bot._vix_velocity_samples}"
    assert bot._vix_velocity_buffer.maxlen == 11, \
        f"buffer.maxlen esperado 11, got {bot._vix_velocity_buffer.maxlen}"
    # Contenido original preservado (5 valores)
    assert list(bot._vix_velocity_buffer) == [14.0, 14.5, 15.0, 15.2, 15.5], \
        f"Contenido buffer no preservado: {list(bot._vix_velocity_buffer)}"
    print(f"    [OK] up_pct: 0.03 → {bot._vix_velocity_threshold_up_pct}")
    print(f"    [OK] down_pct: -0.03 → {bot._vix_velocity_threshold_down_pct}")
    print(f"    [OK] window_seconds: 120 → {bot._vix_velocity_window_seconds}")
    print(f"    [OK] samples: 5 → {bot._vix_velocity_samples} (round(300/30)+1)")
    print(f"    [OK] buffer.maxlen: 5 → {bot._vix_velocity_buffer.maxlen}, contenido preservado")


def test_loop_uses_instance_vars():
    """_vix_velocity_loop debe usar instance vars (sin hardcodeos 0.03/-0.03/5/120s)."""
    print("\n[5] _vix_velocity_loop usa instance vars (sin magic numbers)...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    # Localizar la función
    loop_idx = source.find("async def _vix_velocity_loop")
    assert loop_idx > 0, "_vix_velocity_loop no encontrado"
    next_def = source.find("\n    async def ", loop_idx + 1)
    next_def2 = source.find("\n    def ", loop_idx + 1)
    body_end = min(x for x in [next_def, next_def2] if x > 0)
    body = source[loop_idx:body_end]

    # Debe contener las referencias a instance vars
    assert "self._vix_velocity_samples" in body, "loop no usa _vix_velocity_samples"
    assert "self._vix_velocity_threshold_up_pct" in body, "loop no usa threshold_up_pct"
    assert "self._vix_velocity_threshold_down_pct" in body, "loop no usa threshold_down_pct"
    assert "self._vix_velocity_window_seconds" in body, "loop no usa window_seconds"

    # NO debe contener los magic numbers (en el cuerpo del loop)
    # "< 5" en el contexto de buffer length:
    assert "len(self._vix_velocity_buffer) < 5" not in body, "hardcoded len < 5 sigue presente"
    # ">= 0.03" comparación:
    assert "delta_pct >= 0.03" not in body, "hardcoded 0.03 sigue presente"
    assert "delta_pct <= -0.03" not in body, "hardcoded -0.03 sigue presente"
    # "en 120s" en logs:
    assert "en 120s" not in body, 'log "en 120s" sigue hardcoded'

    print("    [OK] loop usa instance vars; sin hardcodeos 5/0.03/-0.03/120s")


if __name__ == "__main__":
    print("=" * 60)
    print("Sprint S3.1-C smoke test: VIX_VELOCITY thresholds + window")
    print("=" * 60)

    tests = [
        test_constants_present,
        test_instance_vars_init,
        test_helper_branches,
        test_override_apply_logic,
        test_loop_uses_instance_vars,
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
