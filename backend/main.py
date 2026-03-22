"""
main.py — FastAPI application entry point.

Provides:
  - WebSocket /ws  : real-time bidirectional comms with browser UI
  - REST /api/*    : configuration, scores, scenarios, control commands
  - Static /       : serves the frontend HTML/JS

Run with:  python main.py
Or:        uvicorn main:app --host 0.0.0.0 --port 8080
"""

import asyncio
import json
import logging
import os
import sys
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from physics        import ReactorPhysics
from io_bridge      import ModbusBridge
from config_manager import ConfigManager
from persistence    import init_db, save_score, get_leaderboard, \
                           get_setting, set_setting, get_all_settings, \
                           upsert_scenario_meta, delete_scenario_meta, get_scenario_list
from scenario_engine import ScenarioEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# ── Singletons ────────────────────────────────────────────────────────
cfg     = ConfigManager()
physics = ReactorPhysics()
modbus  = ModbusBridge(physics, cfg)
scenario= ScenarioEngine(physics)

# Connected WebSocket clients
_ws_clients: Set[WebSocket] = set()

# ── Broadcast physics state to all browser clients ────────────────────

async def _broadcast():
    global _ws_clients
    if not _ws_clients:
        return
    state = physics.to_dict()
    state["modbus_client_active"] = modbus.client_active
    state["browser_clients"] = len(_ws_clients)
    data = json.dumps(state)
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    _ws_clients -= dead

def _on_tick():
    """Called synchronously by physics after each tick. Schedule broadcast."""
    scenario.tick()
    modbus.update_client_status()
    asyncio.ensure_future(_broadcast())

physics.on_tick(_on_tick)


# ── Lifespan: start background tasks ─────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Restore saved network config if available
    saved_net = get_setting("network_config")
    if saved_net:
        cfg.network = saved_net

    physics_task  = asyncio.create_task(physics.run(), name="physics")
    modbus_task   = asyncio.create_task(modbus.run(),  name="modbus")
    drain_task    = asyncio.create_task(modbus.write_drain_loop(), name="modbus_drain")

    log.info("All services started")
    net = cfg.network
    port = net.get("web_ui", {}).get("port", 8080)
    log.info("Web UI: http://localhost:%d", port)
    log.info("Modbus TCP: %s:%d (unit %d)",
             net.get("modbus_tcp", {}).get("host", "0.0.0.0"),
             net.get("modbus_tcp", {}).get("port", 502),
             net.get("modbus_tcp", {}).get("unit_id", 1))

    yield

    physics.stop()
    for t in [physics_task, modbus_task, drain_task]:
        t.cancel()


app = FastAPI(title="RBMK-1000 Reactor Training Simulator", lifespan=lifespan)


# ── WebSocket endpoint ────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    log.info("Browser connected (%d total)", len(_ws_clients))

    # Send current state immediately on connect
    await ws.send_text(json.dumps(physics.to_dict()))

    try:
        while True:
            raw = await ws.receive_text()
            await _handle_ws_message(raw, ws)
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)
        log.info("Browser disconnected (%d remaining)", len(_ws_clients))


async def _handle_ws_message(raw: str, ws: WebSocket):
    """Handle incoming commands from the browser."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    cmd = msg.get("cmd")

    # ── Physics control ──
    if cmd == "scram":
        physics.cmd_scram()

    elif cmd == "reset":
        physics.cmd_reset()
        scenario.stop()

    elif cmd == "set_sim_speed":
        physics.sim_speed = float(msg.get("speed", 1.0))

    elif cmd == "set_rod_target":
        ok, err = physics.cmd_set_rod_target(msg["rod_id"], msg["target"])
        if not ok:
            await ws.send_text(json.dumps({"type": "error", "msg": err}))

    elif cmd == "set_rod_pos":
        # Direct position override for manual-mode rods via the UI panel
        ok, err = physics.cmd_set_rod_pos(msg["rod_id"], msg["pos"])
        if not ok:
            await ws.send_text(json.dumps({"type": "error", "msg": err}))

    elif cmd == "set_rod_mode":
        physics.cmd_set_rod_mode(msg["rod_id"], msg["mode"])

    elif cmd == "set_pump":
        physics.cmd_set_pump(int(msg["pump_id"]), bool(msg["on"]))

    elif cmd == "set_pump_speed":
        physics.cmd_set_pump_speed(int(msg["pump_id"]), float(msg["speed"]))

    elif cmd == "set_pump_fault":
        physics.cmd_set_pump_fault(int(msg["pump_id"]), bool(msg["fault"]))

    elif cmd == "set_sensor_fault":
        physics.cmd_set_sensor_fault(msg["label"], bool(msg["fault"]))

    elif cmd == "set_fuel":
        physics.cmd_set_fuel(int(msg["ch_idx"]), bool(msg["removed"]))

    elif cmd == "set_difficulty":
        physics.set_difficulty(msg.get("difficulty", "normal"))

    # ── Scenario control ──
    elif cmd == "scenario_load":
        sc_data = cfg.load_scenario(msg["filename"])
        scenario.load(sc_data)

    elif cmd == "scenario_start":
        scenario.start()

    elif cmd == "scenario_stop":
        scenario.stop()

    # ── Score saving ──
    elif cmd == "save_score":
        d = msg.get("data", {})
        save_score(
            mode           = d.get("mode", "free"),
            difficulty     = d.get("difficulty", "normal"),
            shift_duration = int(d.get("shift_duration", 300)),
            score          = int(d.get("score", 0)),
            grade          = d.get("grade", "-"),
            on_target_pct  = d.get("on_target_pct"),
            alarms_fired   = int(d.get("alarms_fired", 0)),
            scrams         = int(d.get("scrams", 0)),
            mwh_generated  = float(d.get("mwh_generated", 0)),
        )


# ── REST API ──────────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    return {
        "running": True,
        "physics_frame": physics.state.frame,
        "sim_speed": physics.sim_speed,
        "modbus_enabled": cfg.network.get("modbus_tcp", {}).get("enabled", False),
        "modbus_port": cfg.network.get("modbus_tcp", {}).get("port", 502),
    }


@app.get("/api/state")
async def api_state():
    return JSONResponse(content=physics.to_dict())


@app.get("/api/config/network")
async def get_network():
    return cfg.network


@app.post("/api/config/network")
async def save_network(data: dict):
    cfg.save_network(data)
    set_setting("network_config", data)
    return {"ok": True}


@app.get("/api/config/iomap")
async def get_iomap():
    return cfg.io_map


@app.post("/api/config/iomap")
async def save_iomap(data: dict):
    cfg.save_io_map(data)
    return {"ok": True}


@app.get("/api/scores")
async def scores(mode: str = None, difficulty: str = None):
    return get_leaderboard()


@app.get("/api/scenarios")
async def list_scenarios():
    files = cfg.list_scenarios()
    meta  = {s["filename"]: s for s in get_scenario_list()}
    result = []
    for f in files:
        m = meta.get(f, {})
        result.append({
            "filename":    f,
            "name":        m.get("name", f),
            "description": m.get("description", ""),
            "type":        m.get("type", "unknown"),
        })
    return result


@app.get("/api/scenarios/{filename}")
async def get_scenario(filename: str):
    try:
        return cfg.load_scenario(filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Scenario not found")


@app.post("/api/scenarios/{filename}")
async def save_scenario(filename: str, data: dict):
    if not filename.endswith(".json"):
        filename += ".json"
    cfg.save_scenario(filename, data)
    upsert_scenario_meta(
        filename    = filename,
        name        = data.get("name", filename),
        description = data.get("description", ""),
        stype       = data.get("type", "scripted"),
    )
    return {"ok": True, "filename": filename}


@app.delete("/api/scenarios/{filename}")
async def delete_scenario(filename: str):
    cfg.delete_scenario(filename)
    delete_scenario_meta(filename)
    return {"ok": True}


# ── Serve frontend ────────────────────────────────────────────────────

@app.get("/")
async def serve_frontend():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        return JSONResponse({"error": "Frontend not found"}, status_code=404)
    return FileResponse(index)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    net  = cfg.network.get("web_ui", {})
    host = net.get("host", "0.0.0.0")
    port = int(net.get("port", 8080))

    # Open the browser in a background thread after a short delay.
    # Using a thread avoids any asyncio event-loop dependency at startup —
    # get_event_loop() raises RuntimeError in Python 3.10+ when called
    # outside a running loop, so we keep the browser launch completely
    # outside asyncio.
    import threading

    def _open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
