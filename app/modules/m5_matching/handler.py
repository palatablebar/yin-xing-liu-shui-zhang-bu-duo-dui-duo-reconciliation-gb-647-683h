"""匹配入口：求解、查看匹配组、确认与取消确认。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from . import service

router = APIRouter(prefix="/api/matches", tags=["匹配"])


@router.get("")
def get_matches(request: Request):
    store = request.app.state.store
    return service.payload(store, store.latest_run())


@router.post("")
def post_matches(request: Request):
    store = request.app.state.store
    return service.run_matching(store, store.latest_run(), request.app.state.settings)


@router.post("/{match_id}/confirm")
def confirm_match(request: Request, match_id: str):
    store = request.app.state.store
    return service.set_confirmation(store, store.latest_run(), match_id, True)


@router.post("/{match_id}/unconfirm")
def unconfirm_match(request: Request, match_id: str):
    store = request.app.state.store
    return service.set_confirmation(store, store.latest_run(), match_id, False)
