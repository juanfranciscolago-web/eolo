#!/usr/bin/env python3
"""FIX smoke: pasar net_debit de eval-time a close_spread.

Verifica que:
  - crop_main._theta_monitor_loop setea pos["_cur_val_valid"] (default False, True solo con marks reales).
  - close_decision incluye "close_debit" con guard.
  - options_trader.execute_decision lee decision.get("close_debit") (contrato no roto).
  - Simulación de la regla del guard:
      _cur_val_valid=True  → close_debit = current_value
      _cur_val_valid=False → close_debit = None
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
EOLO_CROP = os.path.abspath(os.path.join(HERE, "..", "..", "eolo-crop"))


def test_marks_block_sets_valid_flag():
    """Bloque marks: default _cur_val_valid=False + tracking de _s_raw/_l_raw is not None."""
    print("\n[1] Bloque marks tracking de validez...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    # Default antes del try
    assert 'pos["_cur_val_valid"] = False' in source, \
        "Default _cur_val_valid = False antes del try no encontrado"
    # Tracking dentro del try
    assert "_s_raw = short_c.get(\"mark\")" in source, "Tracking _s_raw no encontrado"
    assert "_l_raw = long_c.get(\"mark\")" in source, "Tracking _l_raw no encontrado"
    assert 'pos["_cur_val_valid"] = (_s_raw is not None and _l_raw is not None)' in source, \
        "Asignación _cur_val_valid con condición real no encontrada"
    # Old computation removida: no debe haber "s_mark = short_c.get('mark') or short_c.get('ask') or 0"
    assert "s_mark    = short_c.get(\"mark\") or short_c.get(\"ask\") or 0" not in source, \
        "Vieja lógica s_mark con 'or 0' sigue presente"
    print("    [OK] default False + tracking real + vieja lógica eliminada")


def test_close_decision_has_close_debit():
    """close_decision dict incluye 'close_debit' con guard _cur_val_valid."""
    print("\n[2] close_decision con close_debit...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    expected = '"close_debit":  (pos.get("current_value") if pos.get("_cur_val_valid") else None)'
    assert expected in source, \
        "close_debit con guard _cur_val_valid no encontrado en close_decision dict"
    print("    [OK] close_debit = current_value if _cur_val_valid else None")


def test_options_trader_unchanged_contract():
    """options_trader.execute_decision SIGUE leyendo decision.get('close_debit'); close_spread re-resuelve solo si net_debit is None."""
    print("\n[3] options_trader contract intacto...")
    with open(os.path.join(EOLO_CROP, "execution/options_trader.py")) as f:
        source = f.read()

    assert "net_debit    = decision.get(\"close_debit\")" in source, \
        "execute_decision no lee decision.get('close_debit') con la indentación esperada"
    assert "if net_debit is None:" in source, \
        "close_spread no tiene guard 'if net_debit is None:' para re-resolver"
    print("    [OK] execute_decision lee close_debit + close_spread re-resuelve solo si None")


def test_simulation_valid_path():
    """Simular: pos con marks reales → close_debit = current_value."""
    print("\n[4] Simulación path válido...")

    pos = {"current_value": 0.32, "_cur_val_valid": True}
    close_debit = pos.get("current_value") if pos.get("_cur_val_valid") else None
    assert close_debit == 0.32, f"Esperaba 0.32, got {close_debit}"
    print(f"    [OK] valid=True, current_value=0.32 → close_debit={close_debit}")


def test_simulation_invalid_path():
    """Simular: pos con marks faltantes → close_debit = None (graceful fallback)."""
    print("\n[5] Simulación path inválido (graceful fallback)...")

    pos = {"current_value": 0.0, "_cur_val_valid": False}
    close_debit = pos.get("current_value") if pos.get("_cur_val_valid") else None
    assert close_debit is None, f"Esperaba None, got {close_debit}"
    print(f"    [OK] valid=False, current_value=0.0 → close_debit=None (re-resuelve graceful)")


def test_simulation_zero_with_real_marks():
    """Edge case: cur_val genuinamente 0.0 con marks reales → close_debit = 0.0 (no None)."""
    print("\n[6] Simulación cur_val=0 con marks reales (no enmascarar)...")

    # Caso: short_mark = 0.10, long_mark = 0.10 → diff = 0 pero válido (raro pero posible)
    pos = {"current_value": 0.0, "_cur_val_valid": True}
    close_debit = pos.get("current_value") if pos.get("_cur_val_valid") else None
    assert close_debit == 0.0, f"Esperaba 0.0, got {close_debit}"
    assert close_debit is not None, "close_debit no debe ser None cuando valid=True"
    print(f"    [OK] valid=True con cur_val=0 → close_debit=0.0 (no confundir con None)")


if __name__ == "__main__":
    print("=" * 60)
    print("FIX smoke: close_debit eval-marks plumbing")
    print("=" * 60)

    tests = [
        test_marks_block_sets_valid_flag,
        test_close_decision_has_close_debit,
        test_options_trader_unchanged_contract,
        test_simulation_valid_path,
        test_simulation_invalid_path,
        test_simulation_zero_with_real_marks,
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
