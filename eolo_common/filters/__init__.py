"""
eolo_common.filters — filtros que se aplican sobre señales primarias
antes de enviarlas al decisor (Claude o ejecutor).
"""
from .cross_asset import (  # noqa: F401
    CROSS_ASSET_MAP,
    CrossAssetConfirmation,
    cross_asset_confirmation,
)
