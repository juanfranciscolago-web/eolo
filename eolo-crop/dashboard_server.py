#!/usr/bin/env python3
# ============================================================
#  EOLO v2 — Dashboard Server
#
#  Sirve el dashboard HTML y la API de estado en tiempo real.
#  Corre en paralelo al bot (proceso separado).
#
#  Uso:
#    cd eolo-options
#    python dashboard_server.py
#
#  Dashboard: http://localhost:8765
# ============================================================
import json
import os
from datetime import datetime

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
except ImportError:
    print("Instalá: pip install fastapi uvicorn")
    raise

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
STATE_FILE  = os.path.join(BASE_DIR, "eolo_state.json")
HTML_FILE   = os.path.join(BASE_DIR, "dashboard.html")
PORT        = 8765

app = FastAPI(title="EOLO v2 Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def index():
    if os.path.exists(HTML_FILE):
        with open(HTML_FILE, "r") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>dashboard.html no encontrado</h1>")


@app.get("/api/state")
async def get_state():
    if not os.path.exists(STATE_FILE):
        return JSONResponse({"error": "Bot no corriendo — eolo_state.json no existe"}, status_code=503)
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/health")
async def health():
    state_exists = os.path.exists(STATE_FILE)
    last_update  = None
    stale        = True

    if state_exists:
        try:
            with open(STATE_FILE, "r") as f:
                d = json.load(f)
            ts = d.get("updated_ts", 0)
            import time
            stale = (time.time() - ts) > 120  # stale si no actualizó en 2 min
            last_update = d.get("updated_at")
        except Exception:
            pass

    return JSONResponse({
        "ok":          state_exists and not stale,
        "stale":       stale,
        "last_update": last_update,
    })


if __name__ == "__main__":
    print(f"\n🚀 EOLO v2 Dashboard → http://localhost:{PORT}\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
