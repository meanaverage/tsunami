"""TSUNAMI Web Server — Mission Control.

FastAPI backend with WebSocket for real-time agent visibility.
Streams every iteration of the agent loop to the browser:
tool calls, results, plan state, file changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import Agent
from .config import TsunamiConfig
from .state import Message
from .tools import build_registry
from .tools.plan import set_agent_state

log = logging.getLogger("tsunami.server")

app = FastAPI(title="TSUNAMI", version="1.0.0")

# Active WebSocket connections
connections: list[WebSocket] = []

# Server state
_config: TsunamiConfig | None = None
_agent: Agent | None = None


def get_config() -> TsunamiConfig:
    global _config
    if _config is None:
        ark_dir = Path(__file__).parent.parent
        config_path = ark_dir / "config.yaml"
        _config = TsunamiConfig.from_yaml(str(config_path))
        _config = TsunamiConfig.from_env(_config)
    return _config


async def broadcast(event: dict):
    """Send an event to all connected WebSocket clients."""
    data = json.dumps(event)
    dead = []
    for ws in connections:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.remove(ws)


# ── HTML UI ──

@app.get("/")
async def index():
    html_path = Path(__file__).parent.parent / "ui" / "index.html"
    if html_path.exists():
        return FileResponse(html_path)
    return HTMLResponse("<h1>TSUNAMI</h1><p>UI not found. Place index.html in ark/ui/</p>")


# ── API Routes ──

@app.get("/api/health")
async def health():
    cfg = get_config()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{cfg.model_endpoint}/health")
            model_ok = resp.status_code == 200
    except Exception:
        model_ok = False

    registry = build_registry(cfg, profile=cfg.tool_profile)
    return {
        "status": "ok",
        "model_endpoint": cfg.model_endpoint,
        "model_ok": model_ok,
        "model_name": cfg.model_name,
        "tool_count": len(registry.names()),
        "tool_profile": cfg.tool_profile,
    }


@app.get("/api/tools")
async def list_tools():
    cfg = get_config()
    registry = build_registry(cfg, profile=cfg.tool_profile)
    return {"tools": registry.names(), "count": len(registry.names())}


@app.get("/api/sessions")
async def list_sessions():
    from .session import list_sessions as ls
    cfg = get_config()
    session_dir = Path(cfg.workspace_dir) / ".history"
    return {"sessions": ls(session_dir)}


@app.get("/api/workspace")
async def workspace_files():
    cfg = get_config()
    ws = Path(cfg.workspace_dir)
    files = []
    if ws.exists():
        for f in sorted(ws.rglob("*")):
            if f.is_file() and ".history" not in str(f) and "__pycache__" not in str(f):
                files.append({
                    "path": str(f.relative_to(ws)),
                    "size": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                })
    return {"files": files}


@app.get("/api/ark")
async def ark_info():
    """Info about the Ark — where TSUNAMI came from."""
    ark_dir = Path(__file__).parent.parent
    arc_path = ark_dir / "arc.txt"
    lines = 0
    if arc_path.exists():
        lines = arc_path.read_text().count("\n")

    py_files = list(ark_dir.rglob("*.py"))
    py_files = [f for f in py_files if "__pycache__" not in str(f)]
    total_lines = sum(f.read_text().count("\n") for f in py_files)

    return {
        "name": "TSUNAMI — Agentic Reborn",
        "arc_txt_lines": lines,
        "python_files": len(py_files),
        "python_lines": total_lines,
        "origin": "Reconstructed from an agentic self-documentation artifact, March 2026.",
        "philosophy": "The standing wave does not care what medium it propagates through. It only cares that the frequency is preserved.",
    }


# ── WebSocket — Real-time agent loop ──

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connections.append(ws)
    log.info(f"WebSocket connected. {len(connections)} active.")

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "run":
                task = msg.get("task", "")
                if task:
                    await run_agent_with_streaming(ws, task)

            elif msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        connections.remove(ws)
        log.info(f"WebSocket disconnected. {len(connections)} active.")


async def run_agent_with_streaming(ws: WebSocket, task: str):
    """Run the agent loop, streaming each iteration to the WebSocket."""
    cfg = get_config()
    agent = Agent(cfg)

    # Send start event
    await ws.send_text(json.dumps({
        "type": "start",
        "task": task,
        "timestamp": time.time(),
    }))

    # Monkey-patch the agent to stream iterations
    original_step = agent._step

    async def streaming_step(_watcher_depth=0):
        result = await original_step(_watcher_depth=_watcher_depth)

        # Stream the latest state after each step
        last_msgs = agent.state.conversation[-2:]  # assistant + tool_result
        events = []
        for m in last_msgs:
            event = {
                "type": "iteration",
                "iteration": agent.state.iteration,
                "role": m.role,
                "content": m.content[:1000],
                "timestamp": m.timestamp,
            }
            if m.tool_call:
                func = m.tool_call.get("function", m.tool_call)
                event["tool"] = func.get("name", "")
                event["args"] = func.get("arguments", {})
            events.append(event)

        # Send plan state
        plan_data = None
        if agent.state.plan:
            plan_data = agent.state.plan.to_dict()

        await ws.send_text(json.dumps({
            "type": "step",
            "iteration": agent.state.iteration,
            "events": events,
            "plan": plan_data,
            "complete": agent.state.task_complete,
        }))

        return result

    agent._step = streaming_step

    # Run the agent
    try:
        result = await agent.run(task)
        await ws.send_text(json.dumps({
            "type": "complete",
            "result": result[:5000] if result else "",
            "iterations": agent.state.iteration,
            "timestamp": time.time(),
        }))
    except Exception as e:
        await ws.send_text(json.dumps({
            "type": "error",
            "message": str(e),
            "iteration": agent.state.iteration,
        }))


def start_server(host: str = "0.0.0.0", port: int = 3000):
    """Start the TSUNAMI web server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
