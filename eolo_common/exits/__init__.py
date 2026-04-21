"""
eolo_common.exits — módulos de salida universales.

Contiene componentes de gestión de salida que se aplican sobre cualquier
estrategia después de la entrada. Se importan tanto desde v1 (Bot/),
v1.2 (Bot-v1.2/), v2 (eolo-options/) como desde crypto (eolo-crypto/).
"""
from .adaptive_trailing_stop import (  # noqa: F401
    AdaptiveTrailingStop,
    compute_trailing_stop,
)
