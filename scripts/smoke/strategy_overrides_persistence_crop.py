#!/usr/bin/env python3
"""Sprint S3.X smoke test: persistencia Firestore de strategy overrides.

Verifica que:
  - crop_main tiene _last_overrides_ts init + _load_strategy_overrides_from_firestore
  - main.py POST escribe a eolo-crop-config/strategy_overrides
  - boot llama _load_strategy_overrides_from_firestore después de _load_theta_positions
  - _poll_settings llama _load_strategy_overrides_from_firestore ANTES del guard de settings
  - Simulación: aplicar overrides → instance vars reflejan (reusa lógica S3.1-A/B/...)

NO requiere bot running ni Firestore real.
"""
import sys
import os

# Add eolo-crop to path
HERE = os.path.dirname(os.path.abspath(__file__))
EOLO_CROP = os.path.abspath(os.path.join(HERE, "..", "..", "eolo-crop"))
if EOLO_CROP not in sys.path:
    sys.path.insert(0, EOLO_CROP)

from theta_harvest.theta_harvest_strategy import (
    STOP_LOSS_MULT,
    VIX_SPIKE_DELTA,
)
from theta_harvest.pivot_analysis import DELTA_BY_RISK


def test_main_py_writes_firestore():
    """main.py api_state_edit escribe a eolo-crop-config/strategy_overrides."""
    print("\n[1] main.py POST persiste a Firestore...")
    with open(os.path.join(EOLO_CROP, "main.py")) as f:
        source = f.read()

    assert 'db.collection("eolo-crop-config").document("strategy_overrides").set' in source \
        or '_db.collection("eolo-crop-config").document("strategy_overrides").set' in source, \
        "POST no escribe a eolo-crop-config/strategy_overrides"
    assert '"updated_ts": _t.time()' in source, "updated_ts no se persiste"
    assert '"overrides": dict(bot_instance._strategy_overrides)' in source, \
        "overrides dict no se persiste"
    # safe-call (try/except)
    assert "persist overrides Firestore falló" in source, "safe-call try/except no presente"
    print("    [OK] POST → eolo-crop-config/strategy_overrides con updated_ts + overrides + safe-call")


def test_init_has_last_overrides_ts():
    """crop_main __init__ tiene self._last_overrides_ts: float = 0.0."""
    print("\n[2] __init__ tiene _last_overrides_ts...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert "self._last_overrides_ts:    float        = 0.0" in source, \
        "_last_overrides_ts init no encontrado (revisar indentación/alineación)"
    print("    [OK] self._last_overrides_ts: float = 0.0 presente")


def test_load_method_present():
    """crop_main tiene _load_strategy_overrides_from_firestore con guard updated_ts + apply."""
    print("\n[3] _load_strategy_overrides_from_firestore presente...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert "def _load_strategy_overrides_from_firestore(self):" in source, \
        "método no encontrado"
    # Doc target
    assert 'db.collection("eolo-crop-config").document("strategy_overrides").get()' in source, \
        "lectura del doc strategy_overrides no encontrada"
    # Guard idempotente
    assert "if updated_ts <= self._last_overrides_ts:" in source, \
        "guard por updated_ts no encontrado"
    # Apply via helper
    assert "self._apply_strategy_overrides_to_instance_vars()" in source, \
        "llamada al apply helper no encontrada"
    # Log de éxito
    assert "[StrategyOverrides] Restaurados" in source, \
        "log de éxito no encontrado"
    print("    [OK] método con guard idempotente + apply + log")


def test_boot_callsite():
    """Boot llama _load_strategy_overrides_from_firestore al lado de _load_theta_positions."""
    print("\n[4] Boot callsite...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    # Buscar la región del boot (después del _load_theta_positions y antes del primer "def ")
    boot_idx = source.find("self._load_theta_positions_from_firestore()")
    assert boot_idx > 0, "_load_theta_positions_from_firestore boot callsite no encontrado"
    # La nueva llamada debe estar DESPUÉS y antes de "self.stream.add_handler" o similar
    next_chunk = source[boot_idx:boot_idx + 1000]
    assert "self._load_strategy_overrides_from_firestore()" in next_chunk, \
        "_load_strategy_overrides_from_firestore NO está cerca del boot de theta_positions"
    print("    [OK] boot llama el load (después de theta_positions)")


def test_poll_settings_callsite():
    """_poll_settings llama _load_strategy_overrides_from_firestore ANTES del guard de settings."""
    print("\n[5] _poll_settings callsite (antes del guard de settings)...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    poll_idx = source.find("def _poll_settings(self):")
    assert poll_idx > 0, "_poll_settings no encontrado"
    guard_marker = "if updated_ts <= self._last_settings_ts:"
    guard_idx = source.find(guard_marker, poll_idx)
    assert guard_idx > poll_idx, "guard de settings no encontrado dentro de _poll_settings"

    load_marker = "self._load_strategy_overrides_from_firestore()"
    load_idx = source.find(load_marker, poll_idx)
    assert load_idx > poll_idx, "load NO presente en _poll_settings"
    assert load_idx < guard_idx, \
        f"load (offset={load_idx}) NO está ANTES del guard (offset={guard_idx})"
    print(f"    [OK] load en _poll_settings está antes del guard de settings")


def test_simulated_apply_via_helper():
    """Simular: doc Firestore con overrides → _strategy_overrides.update → apply → instance vars OK.

    Reusa la lógica del helper en un MockBot (sin tocar Firestore real).
    """
    print("\n[6] Simulación apply via helper (lógica del load)...")

    from collections import deque

    class MockBot:
        def __init__(self):
            # Instance vars iniciales (defaults)
            self._stop_loss_mult = STOP_LOSS_MULT
            self._vix_spike_delta = VIX_SPIKE_DELTA
            self._delta_by_risk = {k: list(v) for k, v in DELTA_BY_RISK.items()}
            self._strategy_overrides = {}
            self._last_overrides_ts = 0.0

        def _apply_subset(self):
            """Mini-replica de _apply_strategy_overrides_to_instance_vars (sub-set)."""
            o = self._strategy_overrides
            k = "strategy_params.exits_advanced.stop_loss_mult"
            if k in o:
                try: self._stop_loss_mult = float(o[k])
                except (TypeError, ValueError): pass
            k = "strategy_params.exits_advanced.vix_spike_delta"
            if k in o:
                try: self._vix_spike_delta = float(o[k])
                except (TypeError, ValueError): pass
            prefix = "strategy_params.delta_by_risk."
            for kk, vv in o.items():
                if not kk.startswith(prefix): continue
                rest = kk[len(prefix):]
                if "[" not in rest or not rest.endswith("]"): continue
                level = rest[:rest.index("[")]
                try:
                    idx = int(rest[rest.index("[")+1:-1])
                except (TypeError, ValueError):
                    continue
                if level in self._delta_by_risk and 0 <= idx < len(self._delta_by_risk[level]):
                    try: self._delta_by_risk[level][idx] = float(vv)
                    except (TypeError, ValueError): pass

    bot = MockBot()

    # Simular doc Firestore restaurado
    firestore_doc = {
        "overrides": {
            "strategy_params.exits_advanced.stop_loss_mult": 1.4,
            "strategy_params.exits_advanced.vix_spike_delta": 4.5,
            "strategy_params.delta_by_risk.LOW[0]": 0.18,
        },
        "updated_ts": 12345.0,
    }

    # Lógica del load: guard updated_ts + update + apply
    if firestore_doc.get("updated_ts", 0) > bot._last_overrides_ts:
        bot._last_overrides_ts = firestore_doc["updated_ts"]
        bot._strategy_overrides.update(firestore_doc["overrides"])
        bot._apply_subset()

    assert bot._stop_loss_mult == 1.4, f"stop_loss_mult: {bot._stop_loss_mult}"
    assert bot._vix_spike_delta == 4.5, f"vix_spike_delta: {bot._vix_spike_delta}"
    assert bot._delta_by_risk["LOW"][0] == 0.18, f"delta_by_risk LOW[0]: {bot._delta_by_risk['LOW'][0]}"
    assert bot._last_overrides_ts == 12345.0, f"last_overrides_ts: {bot._last_overrides_ts}"

    # 2do llamado con mismo updated_ts → guard idempotente (no aplica de nuevo)
    bot._stop_loss_mult = 99.0    # tampering manual
    if firestore_doc.get("updated_ts", 0) > bot._last_overrides_ts:
        bot._strategy_overrides.update(firestore_doc["overrides"])
        bot._apply_subset()
    # Como updated_ts == last_overrides_ts, el guard saltea → no se sobrescribe
    assert bot._stop_loss_mult == 99.0, \
        f"Guard idempotente falló: stop_loss_mult sobrescrito a {bot._stop_loss_mult}"
    print("    [OK] aplicación inicial + guard idempotente en re-llamada")


def test_post_note_updated():
    """main.py response 'note' refleja persistencia (ya no 'in-memory only')."""
    print("\n[7] POST response note actualizado...")
    with open(os.path.join(EOLO_CROP, "main.py")) as f:
        source = f.read()

    assert "in-memory only" not in source, \
        "main.py todavía dice 'in-memory only' (el note no se actualizó)"
    assert "persisted to Firestore" in source, \
        "main.py no menciona la persistencia en el note"
    print("    [OK] note refleja la nueva persistencia")


if __name__ == "__main__":
    print("=" * 60)
    print("Sprint S3.X smoke test: persistencia Firestore strategy_overrides")
    print("=" * 60)

    tests = [
        test_main_py_writes_firestore,
        test_init_has_last_overrides_ts,
        test_load_method_present,
        test_boot_callsite,
        test_poll_settings_callsite,
        test_simulated_apply_via_helper,
        test_post_note_updated,
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
