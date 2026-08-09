from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..authentication import User, require_administrator, require_operator
from ..recordings.export import (
    CHANNEL_FILES,
    MAX_EXPORT_BYTES,
    ExportTooLarge,
    ZipExportBuilder,
    parse_timestamp,
    select_channels,
)

router = APIRouter()

# What the replay page actually draws (see frontend/src/stores/replayStore.ts).
# Reading the rest — above all the lidar scans, which dominate a recording —
# only to have the browser discard them is the expensive half of replaying.
REPLAY_CHANNELS = ("pose", "event", "battery")

# Ceiling on the JSON replay response. A recording longer than this replays
# from its start with `truncated: true` rather than failing.
MAX_REPLAY_SAMPLES = 20_000


def _parse_channels(channels: str | None) -> list[str]:
    if channels is None:
        return []
    return [c.strip() for c in channels.split(",") if c.strip()]


# Reading a recording is telemetry, so observers keep it. Starting and stopping
# one is not: recordings are global, so an observer stopping one ends it for
# every operator watching. Deleting destroys shared data outright.
Operator = Annotated[User, Depends(require_operator)]
Administrator = Annotated[User, Depends(require_administrator)]


class StartBody(BaseModel):
    name: str
    # Telemetry channels to capture; unknown names are ignored ("video" is
    # reserved for camera capture, which arrives later).
    channels: list[str] | None = None


@router.get("/api/recordings")
async def list_recordings(request: Request) -> list[dict]:
    return await request.app.state.db.list_recordings()


@router.post("/api/recordings/start")
async def start_recording(request: Request, body: StartBody, user: Operator) -> dict:
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
async def stop_recording(request: Request, user: Operator) -> dict:
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
async def get_recording(
    request: Request,
    recording_id: int,
    channels: str | None = Query(
        default=None,
        description="Comma-separated channels; omit for the replay set "
                    f"({', '.join(REPLAY_CHANNELS)})."),
) -> dict:
    """A recording as JSON, for the replay page.

    Only the channels replay actually draws are read. This endpoint used to
    fetch and JSON-decode *every* sample: a lidar recording's scans dominate
    the row count and the byte count, were decoded on the event loop, and then
    thrown away by the browser, which only consumes pose, event and battery.
    """
    db = request.app.state.db
    row = await db.get_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")

    wanted = _parse_channels(channels) or list(REPLAY_CHANNELS)
    total = await db.count_recording_samples(recording_id, wanted)
    samples: list[dict] = []
    async for sample in db.iter_recording_samples(recording_id, wanted):
        if len(samples) >= MAX_REPLAY_SAMPLES:
            break
        try:
            data = json.loads(sample["data"])
        except (TypeError, ValueError):
            continue
        samples.append({"ts": sample["ts"], "kind": sample["kind"], "data": data})

    row["samples"] = samples
    row["channels_returned"] = wanted
    # A recording longer than the cap replays from its beginning rather than
    # failing outright; the flag lets the UI say so instead of quietly
    # showing a run that stops early for no visible reason.
    row["truncated"] = total > len(samples)
    row["available_sample_count"] = total
    return row


@router.get("/api/recordings/{recording_id}/export.csv")
async def export_recording(request: Request, recording_id: int) -> StreamingResponse:
    """The flat single-table CSV. Genuinely streamed, a page at a time.

    It was previously assembled in a StringIO and then handed to
    StreamingResponse, which streams the sending but not the building — the
    whole recording was in memory before the first byte went out.
    """
    db = request.app.state.db
    row = await db.get_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")

    async def rows():
        yield "ts,kind,x,y,yaw,linear_velocity,voltage,percentage,severity,title,message\n"
        written = 0
        async for sample in db.iter_recording_samples(recording_id):
            try:
                data = json.loads(sample["data"])
            except (TypeError, ValueError):
                continue

            def cell(key: str) -> str:
                value = data.get(key)
                return "" if value is None else str(value)

            def text(key: str) -> str:
                value = data.get(key)
                return "" if value is None else '"' + str(value).replace('"', '""') + '"'

            line = ",".join([
                sample["ts"], sample["kind"],
                cell("x"), cell("y"), cell("yaw"), cell("linear_velocity"),
                cell("voltage"), cell("percentage"),
                cell("severity"), text("title"), text("message"),
            ]) + "\n"
            written += len(line)
            if written > MAX_EXPORT_BYTES:
                # Can't change the status code once the body has started, so
                # say so in the data itself rather than truncating silently.
                yield ("# export truncated: exceeded the "
                       f"{MAX_EXPORT_BYTES} byte limit\n")
                return
            yield line

    filename = f"recording-{recording_id}.csv"
    return StreamingResponse(rows(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/recordings/{recording_id}/export.zip")
async def export_recording_zip(
    request: Request,
    recording_id: int,
    channels: str | None = Query(
        default=None,
        description="Comma-separated channels to include; omit for everything recorded."),
) -> StreamingResponse:
    """A zip of one CSV per channel — see the bundled README for the layout.

    Built in two phases so neither one blocks the server: samples are paged out
    of the database and written to scratch CSVs on the event loop (cheap per
    row, and it yields between pages), then the DEFLATE pass — the expensive
    part — runs in a worker thread. Nothing holds the whole recording, or the
    whole archive, in memory.
    """
    db = request.app.state.db
    row = await db.get_recording(recording_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recording not found")

    wanted: list[str] | None = None
    if channels is not None:
        wanted = _parse_channels(channels)
        unknown = [c for c in wanted if c not in CHANNEL_FILES]
        if unknown:
            raise HTTPException(status_code=400,
                                detail=f"unknown channel(s): {', '.join(unknown)}")
        if not wanted:
            raise HTTPException(status_code=400, detail="no channels selected")

    present = await db.recording_sample_kinds(recording_id)
    selected = select_channels(row, present, wanted)
    t0 = (parse_timestamp(await db.first_sample_ts(recording_id))
          or parse_timestamp(row.get("started_at")))

    builder = ZipExportBuilder(row, selected, t0)
    try:
        async for sample in db.iter_recording_samples(recording_id, selected or None):
            builder.add(sample)
        archive_path = await asyncio.to_thread(builder.finalize)
    except ExportTooLarge as exc:
        builder.close()
        raise HTTPException(
            status_code=413,
            detail=(f"This recording exports to more than "
                    f"{exc.limit // (1024 * 1024)} MB. Select fewer channels "
                    f"(leaving out the laser scan usually does it)."),
        ) from exc
    except Exception:
        builder.close()
        raise

    def stream():
        try:
            with open(archive_path, "rb") as handle:
                while chunk := handle.read(64 * 1024):
                    yield chunk
        finally:
            builder.close()  # the scratch directory goes with it

    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{builder.filename}"',
            "Content-Length": str(os.path.getsize(archive_path)),
        },
    )


@router.delete("/api/recordings/{recording_id}")
async def delete_recording(request: Request, recording_id: int, user: Administrator) -> dict:
    hub = request.app.state.hub
    if hub.recorder.active is not None and hub.recorder.active["id"] == recording_id:
        raise HTTPException(status_code=409, detail="Stop the recording before deleting it.")
    deleted = await request.app.state.db.delete_recording(recording_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="recording not found")
    return {"deleted": recording_id}
