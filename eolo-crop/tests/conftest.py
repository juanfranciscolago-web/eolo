"""Shared pytest fixtures + env compat patches para eolo-crop/tests/.

Compat patch: Flask 2.3 (`app.test_client()`) accede a `werkzeug.__version__`
para setear el HTTP_USER_AGENT header. Werkzeug 3.1+ removió ese attribute
top-level. El env local de Juan tiene esta combinación (Flask 2.3.2 +
Werkzeug 3.1.8) — el container de prod corre con `requirements.txt` donde
flask>=3.0 evita el problema.

Sin este patch, todo `app.test_client()` falla con AttributeError en local.
Aplicamos el patch ANTES de que pytest cargue cualquier test que use el
test_client. Idempotente.
"""
import werkzeug

if not hasattr(werkzeug, "__version__"):
    try:
        from importlib.metadata import version as _pkg_version
        werkzeug.__version__ = _pkg_version("werkzeug")
    except Exception:
        werkzeug.__version__ = "0.0.0-unknown"
