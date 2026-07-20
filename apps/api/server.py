"""Local FastAPI boundary shared by the dashboard and future mobile client."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from apps.api.command_gateway import AgentReply, DashboardCommandGateway, DashboardReply
from apps.dashboard.telemetry import TelemetryFormatError, load_telemetry_snapshot
from apps.gateway.memory_store import MemoryStoreError, delete_memory_fact, list_memory, update_memory_fact
from apps.gateway.pi_agent import PiAgentClient
from brain.memory.briefing import capability_briefing, world_briefing
from brain.memory.graph import knowledge_view
from brain.memory.world_map import map_view
from brain.memory.world_memory import load_world_memory
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, SafetyProfileError, load_safety_profile
from apps.gateway.telegram_mission_gateway import _execute_with_cli, _review_with_cli


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)


class PlanRequest(BaseModel):
    plan_id: str


class MemoryFactRequest(BaseModel):
    category: str = Field(min_length=1, max_length=32)
    fact: str = Field(min_length=1, max_length=240)


def create_app(
    telemetry_path: Path,
    *,
    camera_path: Path | None = None,
    detections_path: Path | None = None,
    down_camera_path: Path | None = None,
    down_detections_path: Path | None = None,
    agent_artifact_dir: Path = Path("simulation/artifacts/agent-missions"),
    memory_dir: Path = Path("var/pi-agent/memory"),
    world_memory_path: Path = Path("var/world-memory/claims.jsonl"),
    safety_profile_path: Path = DEFAULT_SAFETY_PROFILE_PATH,
    gateway: DashboardCommandGateway | None = None,
) -> FastAPI:
    app = FastAPI(title="ByteWolf Command Gateway", version="0.1")
    pi_agent = PiAgentClient()
    # The envelope is read once: it is the same file the SafetyGate loads, and
    # a profile that cannot be read leaves the agent saying it does not know
    # its limits rather than inventing one.
    capabilities = _capability_briefing(safety_profile_path)
    command_gateway = gateway or DashboardCommandGateway(
        converse=lambda session_id, text: AgentReply(
            **pi_agent.converse(
                session_id, text, _world_briefing(world_memory_path), capabilities
            ).__dict__
        ),
        review=_review_with_cli,
        execute=_execute_with_cli,
    )

    @app.get("/api/v1/telemetry")
    def telemetry() -> dict[str, object]:
        try:
            return load_telemetry_snapshot(telemetry_path).as_dict()
        except TelemetryFormatError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/v1/camera")
    def camera(if_none_match: str | None = Header(default=None)) -> Response:
        return _camera_response(camera_path, if_none_match=if_none_match)

    @app.get("/api/v1/cameras/{sensor}")
    def selected_camera(sensor: str, if_none_match: str | None = Header(default=None)) -> Response:
        return _camera_response(_sensor_path(camera_path, down_camera_path, sensor), if_none_match=if_none_match)

    @app.get("/api/v1/detections")
    def detections() -> Response:
        return _detections_response(detections_path)

    @app.get("/api/v1/cameras/{sensor}/detections")
    def selected_detections(sensor: str) -> Response:
        return _detections_response(_sensor_path(detections_path, down_detections_path, sensor))

    @app.get("/api/v1/plans/{plan_id}/status")
    def plan_status(plan_id: str) -> dict[str, str]:
        return _execution_status(agent_artifact_dir, _mission_id(plan_id))

    @app.post("/api/v1/chat")
    def chat(request: ChatRequest, x_bytewolf_session: str = Header(max_length=128)) -> DashboardReply:
        return _handle_gateway(lambda: command_gateway.chat(_session(x_bytewolf_session), request.text))

    @app.post("/api/v1/plans/approve")
    def approve(request: PlanRequest, x_bytewolf_session: str = Header(max_length=128)) -> DashboardReply:
        return _handle_gateway(lambda: command_gateway.approve(_session(x_bytewolf_session), request.plan_id))

    @app.post("/api/v1/plans/cancel")
    def cancel(request: PlanRequest, x_bytewolf_session: str = Header(max_length=128)) -> DashboardReply:
        return _handle_gateway(lambda: command_gateway.cancel(_session(x_bytewolf_session), request.plan_id))

    @app.get("/api/v1/memory")
    def memory(x_bytewolf_session: str = Header(max_length=128)) -> dict[str, object]:
        return list_memory(memory_dir, _session(x_bytewolf_session))

    @app.put("/api/v1/memory/{fact_id}")
    def correct_memory(
        fact_id: str, request: MemoryFactRequest, x_bytewolf_session: str = Header(max_length=128)
    ) -> dict[str, object]:
        try:
            return update_memory_fact(
                memory_dir, _session(x_bytewolf_session), fact_id, category=request.category, fact=request.fact
            )
        except MemoryStoreError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.delete("/api/v1/memory/{fact_id}")
    def erase_memory(fact_id: str, x_bytewolf_session: str = Header(max_length=128)) -> dict[str, object]:
        try:
            return delete_memory_fact(memory_dir, _session(x_bytewolf_session), fact_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/v1/world-memory")
    def world_memory() -> dict[str, object]:
        """Read the evidence-backed world claims; this endpoint never writes.

        World memory is not session-scoped: it describes the shared simulated
        world, not a person.  Contradicted subjects are reported separately so
        the dashboard cannot present disputed evidence as a fact.
        """
        memory = load_world_memory(world_memory_path)
        now = datetime.now(UTC)
        return {
            "claims": [claim.as_dict() for claim in memory.recall(now)],
            "disputed": [claim.as_dict() for claim in memory.disputed(now)],
        }

    @app.get("/api/v1/world-map")
    def world_map() -> dict[str, object]:
        """Read the occupancy grid built from obstacle scans; read-only.

        Cells describe where something *was* measured. There is no free-space
        layer, because neither a clear nor an unobserved sector may be stored.
        """
        memory = load_world_memory(world_memory_path)
        now = datetime.now(UTC)
        cells = map_view(memory.recall(now), memory.disputed(now))
        return {
            "cells": [cell.as_dict() for cell in cells],
            "occupancy_only": True,
        }

    @app.get("/api/v1/knowledge")
    def knowledge(x_bytewolf_session: str = Header(max_length=128)) -> dict[str, object]:
        """Two graphs, never one: personal facts and world evidence stay apart."""
        memory = load_world_memory(world_memory_path)
        now = datetime.now(UTC)
        facts = list_memory(memory_dir, _session(x_bytewolf_session))["facts"]
        return knowledge_view(facts, memory.recall(now), memory.disputed(now))

    web_root = Path(__file__).resolve().parents[1] / "dashboard" / "web"
    app.mount("/", StaticFiles(directory=web_root, html=True), name="dashboard")
    return app


def _capability_briefing(safety_profile_path: Path) -> str:
    """Render the agent's own envelope, or admit it is unknown."""
    try:
        return capability_briefing(load_safety_profile(safety_profile_path))
    except SafetyProfileError:
        return ""


def _world_briefing(world_memory_path: Path) -> str:
    """Resolve the world for one turn, exactly as the dashboard would show it.

    A briefing failure must not cost the user their conversation, so an
    unreadable store means Pi is told it knows nothing rather than the turn
    being refused.
    """
    try:
        memory = load_world_memory(world_memory_path)
        now = datetime.now(UTC)
        return world_briefing(memory.recall(now), memory.disputed(now), now=now)
    except (OSError, ValueError):
        return ""


def _session(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid dashboard session.") from error


def _load_project_environment(path: Path, environment: dict[str, str] | None = None) -> None:
    """Load missing local settings without overriding explicitly exported values."""
    target = os.environ if environment is None else environment
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or key in target:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        target[key] = value


def _sensor_path(front: Path | None, down: Path | None, sensor: str) -> Path | None:
    if sensor == "front":
        return front
    if sensor == "down":
        return down
    raise HTTPException(status_code=404, detail="Unknown camera sensor.")


def _camera_response(path: Path | None, *, if_none_match: str | None = None) -> Response:
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="No camera frame")
    body = path.read_bytes()
    media_type = "image/png" if body[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    version = f'"{path.stat().st_mtime_ns}-{len(body)}"'
    headers = {"Cache-Control": "no-cache", "ETag": version}
    if if_none_match == version:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=media_type, headers=headers)


def _detections_response(path: Path | None) -> Response:
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="No detections")
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-store"})


def _mission_id(value: str) -> str:
    if value.endswith(".mission-spec.json"):
        value = value.removesuffix(".mission-spec.json")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid mission identifier.") from error


def _execution_status(artifact_dir: Path, mission_id: str) -> dict[str, str]:
    """Read the append-only executor decision; never start or control a mission."""
    latest: dict[str, object] | None = None
    for path in artifact_dir.glob("nim-agent-*.json") if artifact_dir.is_dir() else ():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("mission_id") != mission_id:
            continue
        if record.get("model") != "reviewed-plan" or record.get("outcome") not in {"completed", "failed"}:
            continue
        if latest is None or str(record.get("recorded_at", "")) > str(latest.get("recorded_at", "")):
            latest = record
    if latest is None:
        return {"status": "submitted", "message": "A PX4 előellenőrzése és a küldetés fut."}
    if latest["outcome"] == "completed":
        return {"status": "completed", "message": "A küldetés sikeresen befejeződött."}
    reason = str(latest.get("failure_reason", ""))
    if reason.startswith("MissionPreflightError:"):
        return {"status": "failed", "message": f"A PX4 előellenőrzése elutasította a küldetést: {reason.removeprefix('MissionPreflightError: ').strip()}"}
    return {"status": "failed", "message": "A küldetés hibával zárult; a drón nem kapott további parancsot."}


def _handle_gateway(call: object) -> DashboardReply:
    try:
        return call()  # type: ignore[operator]
    except PermissionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the ByteWolf web Command Gateway.")
    parser.add_argument("--telemetry-file", type=Path, default=Path("simulation/artifacts/dashboard/live-telemetry.json"))
    parser.add_argument("--camera-file", type=Path, default=Path("simulation/artifacts/dashboard/camera.jpg"))
    parser.add_argument("--detections-file", type=Path, default=Path("simulation/artifacts/dashboard/detections.json"))
    parser.add_argument("--down-camera-file", type=Path, default=Path("simulation/artifacts/dashboard/camera-down.jpg"))
    parser.add_argument("--down-detections-file", type=Path, default=Path("simulation/artifacts/dashboard/detections-down.json"))
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    _load_project_environment(Path(__file__).resolve().parents[2] / ".env")
    import uvicorn
    uvicorn.run(
        create_app(
            args.telemetry_file, camera_path=args.camera_file, detections_path=args.detections_file,
            down_camera_path=args.down_camera_file, down_detections_path=args.down_detections_file,
        ), host="127.0.0.1", port=args.port,
    )


if __name__ == "__main__":
    main()
