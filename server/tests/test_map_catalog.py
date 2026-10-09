from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app
from app.settings import Settings
from app.telemetry.map_catalog import MapCatalog


def test_local_catalog_endpoints_and_revisions(tmp_path):
    app = create_app(Settings(auth_mode="none", database_path=str(tmp_path / "test.db")))
    with TestClient(app) as client:
        catalog = client.get("/api/maps").json()
        assert {m["map_id"] for m in catalog} == {"cmuq-floor1", "cmuq-floor2"}
        for meta in catalog:
            assert "rle" not in meta
            result = client.get("/api/maps/" + meta["map_id"])
            assert result.status_code == 200
            data = result.json()
            assert data["map_revision"] == meta["map_revision"]
            assert sum(run[1] for run in data["rle"]) == data["width"] * data["height"]
        assert client.get("/api/maps/unknown").status_code == 404
        assert client.get("/api/map").status_code == 200


async def test_browser_disconnect_cancels_only_its_map_transaction():
    import json
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock
    from app.commands.broker import CommandBroker, ActiveCommand
    owner, other = object(), object()
    socket = NS(send_text=AsyncMock())
    hub = NS(settings=Settings(), robots={"robot": NS(websocket=socket)}, _next_seq=lambda: 1)
    broker = CommandBroker(hub)
    broker.active = {
        "map": ActiveCommand("map", "set_initial_pose", "robot", initiator=owner),
        "nav": ActiveCommand("nav", "navigate_to_pose", "robot", initiator=owner),
        "other": ActiveCommand("other", "set_initial_pose", "robot", initiator=other),
    }
    await broker.cancel_for_client(owner)
    socket.send_text.assert_awaited_once()
    frame = json.loads(socket.send_text.call_args.args[0])
    assert frame["type"] == "command.cancel" and frame["data"] == {"command_id": "map"}
