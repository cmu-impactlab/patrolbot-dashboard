from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..authentication import User, get_current_user

router = APIRouter()


class LayoutBody(BaseModel):
    widgets: list[str]
    layouts: dict[str, list[dict[str, Any]]]
    preset: str | None = None


@router.get("/api/layouts")
async def list_layouts(request: Request, user: Annotated[User, Depends(get_current_user)]) -> list[dict]:
    return await request.app.state.db.get_layouts(user.id)


@router.get("/api/layouts/{name}")
async def get_layout(request: Request, name: str, user: Annotated[User, Depends(get_current_user)]) -> dict:
    layout = await request.app.state.db.get_layout(user.id, name)
    if layout is None:
        raise HTTPException(status_code=404, detail="layout not found")
    return layout


@router.put("/api/layouts/{name}")
async def save_layout(request: Request, name: str, body: LayoutBody,
                      user: Annotated[User, Depends(get_current_user)]) -> dict:
    await request.app.state.db.save_layout(user.id, name, body.model_dump())
    return {"saved": name}


@router.delete("/api/layouts/{name}")
async def delete_layout(request: Request, name: str,
                        user: Annotated[User, Depends(get_current_user)]) -> dict:
    deleted = await request.app.state.db.delete_layout(user.id, name)
    if not deleted:
        raise HTTPException(status_code=404, detail="layout not found or is a preset")
    return {"deleted": name}
