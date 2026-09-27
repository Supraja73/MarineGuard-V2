"""
websocket.py (router)
-------------------------
A single websocket endpoint that streams live platform events (new
positions, new alerts, new ships, new blocks) to any connected frontend
client, using the shared ConnectionRegistry from app.core.dependencies.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    registry = websocket.app.state.connection_registry
    await websocket.accept()
    registry.add(websocket)
    try:
        while True:
            # We don't expect client -> server messages, but keep the
            # connection alive and detect disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        registry.remove(websocket)
