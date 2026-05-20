#!/usr/bin/env python3
"""Sprint S3.4 smoke test: VIX_CREDIT_TABLE editable (list-of-lists override).

6 filas × 5 cols editables (spy/qqq/iwm/tqqq/payoff_mult); vix_ceil NO editable.
Default=None → fallback al módulo (comportamiento idéntico).

NO requiere bot running.
"""
import sys
import os
import inspect
import math

# Add eolo-crop to path
HERE = os.path.dirname(os.path.abspath(__file__))
EOLO_CROP = os.path.abspath(os.path.join(HERE, "..", "..", "eolo-crop"))
if EOLO_CROP not in sys.path:
    sys.path.insert(0, EOLO_CROP)

from theta_harvest.theta_harvest_strategy import (
    VIX_CREDIT_TABLE,
    scan_theta_harvest,
    scan_theta_harvest_tranches,
    _vix_credit_thresholds,
)


def test_signatures():
    """3 fns con kwarg vix_credit_table (default None)."""
    print("\n[1] Signatures con vix_credit_table...")
    for fn, name in [(_vix_credit_thresholds, "_vix_credit_thresholds"),
                     (scan_theta_harvest, "scan_theta_harvest"),
                     (scan_theta_harvest_tranches, "scan_theta_harvest_tranches")]:
        sig = inspect.signature(fn)
        assert "vix_credit_table" in sig.parameters, f"{name} sin kwarg vix_credit_table"
        default = sig.parameters["vix_credit_table"].default
        assert default is None, f"{name}.vix_credit_table default esperado None, got {default}"
        print(f"    [OK] {name}.vix_credit_table default=None")


def test_loop_uses_fallback():
    """_vix_credit_thresholds loop debe usar (vix_credit_table or VIX_CREDIT_TABLE)."""
    print("\n[2] Loop con fallback...")
    with open(os.path.join(EOLO_CROP, "theta_harvest/theta_harvest_strategy.py")) as f:
        source = f.read()
    assert "for vix_ceil, spy_min, qqq_min, iwm_min, tqqq_min, payoff_mult in (vix_credit_table or VIX_CREDIT_TABLE):" in source, \
        "Loop con fallback no encontrado"
    print("    [OK] for ... in (vix_credit_table or VIX_CREDIT_TABLE)")


def test_instance_var_init():
    """crop_main: self._vix_credit_table init desde VIX_CREDIT_TABLE (list de listas)."""
    print("\n[3] CropBotTheta._vix_credit_table init...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()
    assert "VIX_CREDIT_TABLE," in source, "VIX_CREDIT_TABLE no importado en crop_main"
    assert "self._vix_credit_table: list = [list(row) for row in VIX_CREDIT_TABLE]" in source, \
        "Init self._vix_credit_table (list de listas) no encontrado"
    print("    [OK] import + init list de listas presentes")


def test_helper_parse_block():
    """_apply tiene bloque parse 'vix_credit_table[<row>].<col>'."""
    print("\n[4] Helper _apply bloque parse...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()

    assert 'prefix = "strategy_params.vix_credit_table["' in source, \
        "prefix vix_credit_table no encontrado"
    assert 'col_idx = {"spy": 1, "qqq": 2, "iwm": 3, "tqqq": 4, "payoff_mult": 5}' in source, \
        "col_idx mapping no encontrado"
    assert 'row_str, col = rest.split("].", 1)' in source, "split bracket+dot no encontrado"
    assert "self._vix_credit_table[row][col_idx[col]] = float(val)" in source, \
        "asignación a [row][col_idx] no encontrada"
    print("    [OK] bloque parse bracket+dot completo (todos float)")


def test_override_apply_logic():
    """Simular 2 overrides → instance var actualizada en celdas correctas."""
    print("\n[5] Override apply logic simulation...")

    bot_table = [list(row) for row in VIX_CREDIT_TABLE]
    initial_row3 = list(bot_table[3])
    initial_row0 = list(bot_table[0])
    initial_row5 = list(bot_table[5])  # última fila

    overrides = {
        "strategy_params.vix_credit_table[3].spy":         0.45,
        "strategy_params.vix_credit_table[0].payoff_mult": 0.60,
    }

    # Replica del bloque del helper
    prefix = "strategy_params.vix_credit_table["
    col_idx = {"spy": 1, "qqq": 2, "iwm": 3, "tqqq": 4, "payoff_mult": 5}
    for k, val in overrides.items():
        if not k.startswith(prefix):
            continue
        rest = k[len(prefix):]
        if "]." not in rest:
            continue
        row_str, col = rest.split("].", 1)
        try:
            row = int(row_str)
        except (TypeError, ValueError):
            continue
        if col in col_idx and 0 <= row < len(bot_table):
            try:
                bot_table[row][col_idx[col]] = float(val)
            except (TypeError, ValueError):
                pass

    # Cambios esperados
    assert bot_table[3][1] == 0.45, f"row[3][1] (spy): {bot_table[3][1]}"
    assert bot_table[0][5] == 0.60, f"row[0][5] (payoff_mult): {bot_table[0][5]}"
    # Lo que NO se tocó (resto de la fila 3, resto de la fila 0, toda la fila 5)
    for i in (0, 2, 3, 4, 5):
        assert bot_table[3][i] == initial_row3[i], \
            f"row[3][{i}] cambió: {bot_table[3][i]} vs {initial_row3[i]}"
    for i in (0, 1, 2, 3, 4):
        assert bot_table[0][i] == initial_row0[i], \
            f"row[0][{i}] cambió: {bot_table[0][i]} vs {initial_row0[i]}"
    assert bot_table[5] == initial_row5, \
        f"row[5] (última) cambió: {bot_table[5]} vs {initial_row5}"

    print(f"    [OK] row[3].spy: {VIX_CREDIT_TABLE[3][1]} → 0.45")
    print(f"    [OK] row[0].payoff_mult: {VIX_CREDIT_TABLE[0][5]} → 0.60")
    print(f"    [OK] resto de fila 3, fila 0 y fila 5 (última) untouched")


def test_last_row_vix_ceil_inf():
    """La última fila preserva vix_ceil = inf (no editable, fallback open-ended)."""
    print("\n[6] Última fila conserva vix_ceil=inf...")
    bot_table = [list(row) for row in VIX_CREDIT_TABLE]
    last_vix_ceil = bot_table[-1][0]
    assert math.isinf(last_vix_ceil), \
        f"última fila vix_ceil esperado inf, got {last_vix_ceil}"

    # Allowlist en main.py excluye vix_ceil; verificar que NO existe en col_idx
    col_idx = {"spy": 1, "qqq": 2, "iwm": 3, "tqqq": 4, "payoff_mult": 5}
    assert "vix_ceil" not in col_idx, "vix_ceil NO debe estar en col_idx (no editable)"
    print(f"    [OK] vix_ceil ({last_vix_ceil}) NO editable, col_idx excluye 'vix_ceil'")


def test_callsite_threading():
    """Callsite ENTRY pasa vix_credit_table = self._vix_credit_table."""
    print("\n[7] Callsite ENTRY threading...")
    with open(os.path.join(EOLO_CROP, "crop_main.py")) as f:
        source = f.read()
    assert "vix_credit_table      = self._vix_credit_table," in source, \
        "Threading vix_credit_table no encontrado en _run_theta_harvest callsite"
    print("    [OK] ENTRY pasa vix_credit_table = self._vix_credit_table")


def test_pivot_analysis_untouched():
    """No se modificó pivot_analysis.py."""
    print("\n[8] pivot_analysis.py untouched...")
    with open(os.path.join(EOLO_CROP, "theta_harvest/pivot_analysis.py")) as f:
        source = f.read()
    assert "vix_credit_table" not in source, "vix_credit_table apareció en pivot_analysis"
    print("    [OK] pivot_analysis.py no contiene vix_credit_table")


if __name__ == "__main__":
    print("=" * 60)
    print("Sprint S3.4 smoke test: vix_credit_table editable (list-of-lists)")
    print("=" * 60)

    tests = [
        test_signatures,
        test_loop_uses_fallback,
        test_instance_var_init,
        test_helper_parse_block,
        test_override_apply_logic,
        test_last_row_vix_ceil_inf,
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
