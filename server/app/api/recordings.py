from __future__ import annotations

import io
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from ..recordings.export import CHANNEL_FILES, build_zip

router = APIRouter()


class StartBody(BaseModel):
    name: str
    # Telemetry channels to capture; unknown names are ignored ("video" is
    # reserved for camera capture, which arrives later).
    channels: list[str] | None = None


@router.get("/api/recordings")
async def list_recordings(request: Request) -> list[dict]:
    return await request.app.state.db.list_recordings()


@router.post("/api/recordings/start")
async def start_recording(request: Request, body: StartBody) -> dict:
    hub = request.app.state.hub
    session = hub.primary()
    if session is None or session.state.connection == "offline":
        raise HTTPException(status_code=409, detail="The robot is not connected — nothing to record.")
    name = body.name.strip() or "Untitled recording"
    row = await hub.recorder.start(session.robot_id, name, body.channels)
    if row is None:
        raise HTTPException(status_code=409, detail="A recording is already in progress.")
    session.state.recording = True
    await hub.emit_event(session, "info", "Recording started", f"Now recording “{name}”.")
    hub._emit_derived(session)
    return row


@router.post("/api/recordings/stop")
async def stop_recording(request: Request) -> dict:
    hub = request.app.state.hub
    row = await hub.recorder.stop()
    if row is None:
        raise HTTPException(status_code=409, detail="No recording is in progress.")
    session = hub.primary()
    if session is not None:
        session.state.recording = False
        await hub.emit_event(session, "info", "Recording finished",
                             f"“{row['name']}” saved with {row['sample_count']} samples.")
        hub._emit_derived(session)
    return row


@router.get("/api/recordings/{recording_id}")
async def get_recording(request: Request, recording_id: int) -> dict:
    db = request.app.state.db
    row = await db.get_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")
    samples = await db.get_recording_samples(recording_id)
    row["samples"] = [{"ts": s["ts"], "kind": s["kind"], "data": json.loads(s["data"])} for s in samples]
    return row


@router.get("/api/recordings/{recording_id}/export.csv")
async def export_recording(request: Request, recording_id: int) -> StreamingResponse:
    db = request.app.state.db
    row = await db.get_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")
    samples = await db.get_recording_samples(recording_id)

    buffer = io.StringIO()
    buffer.write("ts,kind,x,y,yaw,linear_velocity,voltage,percentage,severity,title,message\n")
    for sample in samples:
        data = json.loads(sample["data"])
        def cell(key: str) -> str:
            value = data.get(key)
            return "" if value is None else str(value)
        def text(key: str) -> str:
            value = data.get(key)
            return "" if value is None else '"' + str(value).replace('"', '""') + '"'
        buffer.write(",".join([
            sample["ts"], sample["kind"],
            cell("x"), cell("y"), cell("yaw"), cell("linear_velocity"),
            cell("voltage"), cell("percentage"),
            cell("severity"), text("title"), text("message"),
        ]) + "\n")
    buffer.seek(0)
    filename = f"recording-{recording_id}.csv"
    return StreamingResponse(buffer, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/recordings/{recording_id}/export.zip")
async def export_recording_zip(
    request: Request,
    recording_id: int,
    channels: str | None = Query(
        default=None,
        description="Comma-separated channels to include; omit for everything recorded."),
) -> Response:
    """A zip of one CSV per channel — see the bundled README for the layout."""
    db = request.app.state.db
    row = await db.get_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")

    wanted: list[str] | None = None
    if channels is not None:
        wanted = [c.strip() for c in channels.split(",") if c.strip()]
        unknown = [c for c in wanted if c not in CHANNEL_FILES]
        if unknown:
            raise HTTPException(status_code=400,
                                detail=f"unknown channel(s): {', '.join(unknown)}")
        if not wanted:
            raise HTTPException(status_code=400, detail="no channels selected")

    samples = await db.get_recording_samples(recording_id)
    payload, filename = build_zip(row, samples, wanted)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/api/recordings/{recording_id}")
async def delete_recording(request: Request, recording_id: int) -> dict:
    hub = request.app.state.hub
    if hub.recorder.active is not None and hub.recorder.active["id"] == recording_id:
        raise HTTPException(status_code=409, detail="Stop the recording before deleting it.")
    deleted = await request.app.state.db.delete_recording(recording_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="recording not found")
    return {"deleted": recording_id}
