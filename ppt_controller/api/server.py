"""
FastAPI Backend with WebSocket support for real-time presentation control.
"""

import asyncio
import logging
import json
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Set, Any
from dataclasses import asdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ppt_controller.config import get_config
from ppt_controller.core.state import get_state, StateManager
from ppt_controller.core.ppt_com import get_ppt_controller
from ppt_controller.voice.pipeline import get_voice_pipeline
from ppt_controller.ai.similarity import get_similarity_monitor

logger = logging.getLogger(__name__)


# Request/Response models
class CommandRequest(BaseModel):
    command: str
    target: Optional[int] = None
    origin: str = "HTTP"


class DeckSwitchRequest(BaseModel):
    deck_id: str


class AnnotationRequest(BaseModel):
    stroke_id: str
    tool: str
    color: str
    width: int
    points: List[Dict[str, float]]
    slide_index: int


def _serialize_strokes(deck, slide_index: int) -> List[Dict]:
    """Return a browser-safe representation of one slide's annotations."""
    if not deck or not 0 <= slide_index < len(deck.slides):
        return []
    return [
        {
            "id": stroke.id,
            "tool": stroke.tool,
            "color": stroke.color,
            "width": stroke.width,
            "points": [
                {"x": point.x, "y": point.y, "pressure": point.pressure}
                for point in stroke.points
            ],
            "slide_index": stroke.slide_index,
            "completed": stroke.completed,
        }
        for stroke in deck.slides[slide_index].annotations
    ]


class SyncResponse(BaseModel):
    current_slide: int
    total_slides: int
    presentation_name: str
    mode: str
    connection_state: str
    active_deck: Optional[str]
    decks: List[Dict]
    annotations: Dict[int, List[Dict]]


# Connection manager for WebSockets
class ConnectionManager:
    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info(f"WebSocket connected. Total: {len(self._connections)}")
    
    def disconnect(self, websocket: WebSocket):
        asyncio.create_task(self._disconnect_async(websocket))
    
    async def _disconnect_async(self, websocket: WebSocket):
        async with self._lock:
            self._connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self._connections)}")
    
    async def broadcast(self, message: Dict):
        if not self._connections:
            return
        
        dead = set()
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        
        for ws in dead:
            self._connections.discard(ws)


manager = ConnectionManager()

# Strokes removed by undo, keyed by deck and slide so redo cannot leak across
# presentations. A new stroke or clear operation invalidates that slide's redo.
_annotation_redo: Dict[tuple, List[Any]] = {}


# State callbacks
async def on_state_change(state):
    await manager.broadcast({"type": "state_change", "data": state})


async def on_deck_change(deck_id: str):
    state_mgr = get_state()
    await manager.broadcast({
        "type": "deck_change",
        "data": {"active_deck": deck_id, "decks": state_mgr.get_deck_list()}
    })


async def on_slide_change(old_slide: int, new_slide: int):
    await manager.broadcast({
        "type": "slide_change",
        "data": {"old": old_slide, "new": new_slide}
    })


async def on_annotation_added(deck_id: str, slide_index: int, stroke):
    await manager.broadcast({
        "type": "annotation_added",
        "data": {
            "deck_id": deck_id,
            "slide_index": slide_index,
            "stroke": {
                "id": stroke.id,
                "tool": stroke.tool,
                "color": stroke.color,
                "width": stroke.width,
                "points": [{"x": p.x, "y": p.y, "pressure": p.pressure} for p in stroke.points],
                "completed": stroke.completed
            }
        }
    })


async def on_transcript_final(text: str):
    await manager.broadcast({"type": "transcript_final", "data": {"text": text}})


async def on_transcript_interim(text: str):
    await manager.broadcast({"type": "transcript_interim", "data": {"text": text}})


# App lifecycle
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    cfg = get_config()
    
    # Initialize state manager
    state_mgr = get_state()
    state_mgr.on_state_change = on_state_change
    state_mgr.on_deck_change = on_deck_change
    state_mgr.on_slide_change = on_slide_change
    state_mgr.on_annotation_added = on_annotation_added
    
    # Initialize PowerPoint controller
    ppt = get_ppt_controller()
    ppt.start()
    ppt.add_state_callback(lambda s: asyncio.create_task(manager.broadcast({"type": "ppt_state", "data": s})))
    
    # Initialize voice pipeline
    voice = get_voice_pipeline()
    voice.add_transcript_callback(lambda t: asyncio.create_task(
        on_transcript_final(t.text) if t.is_final else on_transcript_interim(t.text)
    ))
    if not voice.start():
        logger.warning("Voice pipeline failed to start")
    
    # Initialize similarity monitor
    similarity = get_similarity_monitor()
    similarity.set_advance_callback(lambda slide, info: asyncio.create_task(
        handle_auto_advance(slide, info)
    ))
    similarity.start()
    
    # Initial sync
    await asyncio.sleep(1)
    ppt_state = ppt.get_state_sync()
    if ppt_state.get("connected"):
        ppt.export_thumbnails_sync()
    
    logger.info("Backend started successfully")
    
    yield
    
    # Shutdown
    similarity.stop()
    voice.stop()
    ppt.stop()
    logger.info("Backend shutdown complete")


app = FastAPI(
    title="Presentation Controller API",
    version="2.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}


# Sync endpoint
@app.get("/sync", response_model=SyncResponse)
async def get_sync():
    state_mgr = get_state()
    ppt = get_ppt_controller()
    
    ppt_state = ppt.get_state_sync()
    
    deck = state_mgr.active_deck
    annotations = {}
    if deck:
        for i, slide in enumerate(deck.slides):
            if slide.annotations:
                annotations[i] = [
                    {
                        "id": s.id,
                        "tool": s.tool,
                        "color": s.color,
                        "width": s.width,
                        "points": [{"x": p.x, "y": p.y, "pressure": p.pressure} for p in s.points],
                        "completed": s.completed
                    }
                    for s in slide.annotations
                ]
    
    return SyncResponse(
        current_slide=ppt_state.get("current_slide", 1),
        total_slides=ppt_state.get("total_slides", 0),
        presentation_name=ppt_state.get("presentation", "Unknown"),
        mode=ppt_state.get("mode", "disconnected"),
        connection_state=state_mgr.connection_state.value,
        active_deck=state_mgr._active_deck_id,
        decks=state_mgr.get_deck_list(),
        annotations=annotations
    )


# Command endpoint
@app.post("/command")
async def execute_command(cmd: CommandRequest):
    ppt = get_ppt_controller()
    state_mgr = get_state()
    
    command = cmd.command.upper()
    target = cmd.target
    
    try:
        if command == "NEXT":
            result = ppt.next_slide_sync()
        elif command == "PREVIOUS":
            result = ppt.previous_slide_sync()
        elif command == "JUMP" and target is not None:
            result = ppt.jump_to_sync(target)
        elif command == "BLACKOUT":
            result = ppt.toggle_blackout_sync()
        elif command == "LASER":
            # Laser is handled via COM directly
            result = {"success": True}
        elif command == "SYNC":
            ppt.export_thumbnails_sync()
            result = {"success": True}
        elif command == "FOCUS":
            result = ppt.focus_window().result(timeout=5.0)
        else:
            raise HTTPException(400, f"Unknown command: {command}")
        
        return {"status": "ok", "result": result, "latency_ms": 0}
    
    except Exception as e:
        logger.error(f"Command error: {e}")
        raise HTTPException(500, str(e))


# Deck management
@app.get("/decks")
async def list_decks():
    state_mgr = get_state()
    return {"decks": state_mgr.get_deck_list()}


@app.post("/decks/switch")
async def switch_deck(req: DeckSwitchRequest):
    state_mgr = get_state()
    if state_mgr.set_active_deck(req.deck_id):
        return {"status": "ok", "active_deck": req.deck_id}
    raise HTTPException(404, "Deck not found")


@app.post("/decks/{deck_id}/export")
async def export_deck_thumbnails(deck_id: str):
    ppt = get_ppt_controller()
    result = ppt.export_thumbnails_sync()
    return {"status": "ok", "result": result}


@app.post("/decks/navigate")
async def navigate_deck(req: dict):
    """Navigate to next/previous deck."""
    state_mgr = get_state()
    direction = req.get("direction", "next")
    decks = state_mgr.get_deck_list()
    if not decks:
        return {"status": "error", "message": "No decks available"}
    
    active_idx = next((i for i, d in enumerate(decks) if d["is_active"]), 0)
    if direction == "next":
        new_idx = (active_idx + 1) % len(decks)
    else:
        new_idx = (active_idx - 1) % len(decks)
    
    new_deck_id = decks[new_idx]["id"]
    if state_mgr.set_active_deck(new_deck_id):
        return {"status": "ok", "active_deck": new_deck_id}
    raise HTTPException(404, "Deck not found")


@app.post("/decks/create")
async def create_deck(req: dict):
    """Create a new deck from current PowerPoint."""
    state_mgr = get_state()
    ppt = get_ppt_controller()
    
    name = req.get("name", f"Deck {len(state_mgr.get_deck_list()) + 1}")
    # Export thumbnails for current presentation
    ppt.export_thumbnails_sync()
    
    # Create deck state from current PowerPoint
    ppt_state = ppt.get_state_sync()
    slides = []
    total_slides = max(ppt_state.get("total_slides", 0), 1)  # At least 1 slide
    for i in range(total_slides):
        slides.append({
            "index": i,
            "title": f"Slide {i+1}",
            "thumbnail_path": f"/static/thumbnails/slide_{i+1}.png"
        })
    
    from ppt_controller.core.state import DeckState, SlideInfo
    deck = DeckState(
        name=name,
        path=ppt_state.get("presentation", ""),
        slides=[SlideInfo(index=s["index"], title=s["title"], thumbnail_path=s["thumbnail_path"]) for s in slides],
        current_slide=ppt_state.get("current_slide", 1) - 1,
        total_slides=len(slides),
        is_active=True
    )
    
    deck_id = state_mgr.add_deck(deck)
    return {"status": "ok", "deck_id": deck_id}


# Annotations
@app.post("/annotations")
async def add_annotation(req: AnnotationRequest):
    state_mgr = get_state()
    deck = state_mgr.active_deck
    if not deck:
        raise HTTPException(400, "No active deck")
    
    # Convert points
    from ppt_controller.core.state import AnnotationPoint, AnnotationStroke
    points = [AnnotationPoint(x=p["x"], y=p["y"], pressure=p.get("pressure", 1.0)) for p in req.points]
    
    stroke = AnnotationStroke(
        id=req.stroke_id,
        tool=req.tool,
        color=req.color,
        width=req.width,
        points=points,
        slide_index=req.slide_index
    )
    
    if not 0 <= req.slide_index < len(deck.slides):
        raise HTTPException(400, "Slide index is out of range")

    _annotation_redo.pop((state_mgr._active_deck_id, req.slide_index), None)
    state_mgr.add_annotation(state_mgr._active_deck_id, req.slide_index, stroke)
    return {"status": "ok", "strokes": _serialize_strokes(deck, req.slide_index)}


@app.get("/annotations/{slide_index}")
async def get_active_deck_annotations(slide_index: int):
    """Load annotations for one slide in the active deck."""
    state_mgr = get_state()
    deck = state_mgr.active_deck
    if not deck:
        return {"strokes": []}
    if not 0 <= slide_index < len(deck.slides):
        raise HTTPException(400, "Slide index is out of range")
    return {"strokes": _serialize_strokes(deck, slide_index)}


@app.delete("/annotations/{slide_index}")
async def clear_active_deck_annotations(slide_index: int):
    """Clear one slide in the active deck (used by the Socket.IO relay)."""
    state_mgr = get_state()
    deck = state_mgr.active_deck
    if not deck:
        raise HTTPException(400, "No active deck")
    if not 0 <= slide_index < len(deck.slides):
        raise HTTPException(400, "Slide index is out of range")
    _annotation_redo.pop((state_mgr._active_deck_id, slide_index), None)
    state_mgr.clear_annotations(state_mgr._active_deck_id, slide_index)
    return {"status": "ok", "strokes": []}


@app.post("/annotations/undo")
async def undo_active_deck_annotation(req: Dict[str, int]):
    """Undo the last annotation on one active-deck slide."""
    slide_index = req.get("slide_index")
    state_mgr = get_state()
    deck = state_mgr.active_deck
    if not deck:
        raise HTTPException(400, "No active deck")
    if not isinstance(slide_index, int) or not 0 <= slide_index < len(deck.slides):
        raise HTTPException(400, "Slide index is out of range")
    if deck.slides[slide_index].annotations:
        removed = deck.slides[slide_index].annotations.pop()
        _annotation_redo.setdefault((state_mgr._active_deck_id, slide_index), []).append(removed)
    return {"status": "ok", "strokes": _serialize_strokes(deck, slide_index)}


@app.post("/annotations/redo")
async def redo_active_deck_annotation(req: Dict[str, int]):
    """Restore the latest undone stroke on one active-deck slide."""
    slide_index = req.get("slide_index")
    state_mgr = get_state()
    deck = state_mgr.active_deck
    if not deck:
        raise HTTPException(400, "No active deck")
    if not isinstance(slide_index, int) or not 0 <= slide_index < len(deck.slides):
        raise HTTPException(400, "Slide index is out of range")

    redo_stack = _annotation_redo.get((state_mgr._active_deck_id, slide_index), [])
    if redo_stack:
        stroke = redo_stack.pop()
        state_mgr.add_annotation(state_mgr._active_deck_id, slide_index, stroke)
    return {"status": "ok", "strokes": _serialize_strokes(deck, slide_index)}


@app.delete("/annotations/{deck_id}/{slide_index}")
async def clear_annotations(deck_id: str, slide_index: int):
    state_mgr = get_state()
    state_mgr.clear_annotations(deck_id, slide_index)
    _annotation_redo.pop((deck_id, slide_index), None)
    return {"status": "ok"}


# Voice pipeline control
@app.get("/voice/status")
async def voice_status():
    voice = get_voice_pipeline()
    return voice.get_status()


@app.get("/debug/deck")
async def debug_deck():
    state_mgr = get_state()
    deck = state_mgr.active_deck
    if not deck:
        return {"error": "No active deck"}
    return {
        "deck_id": state_mgr._active_deck_id,
        "deck_name": deck.name,
        "total_slides": deck.total_slides,
        "slides_count": len(deck.slides),
        "slides": [
            {
                "index": s.index,
                "title": s.title,
                "annotations_count": len(s.annotations)
            }
            for s in deck.slides
        ]
    }


@app.post("/voice/switch/{backend}")
async def switch_voice_backend(backend: str):
    voice = get_voice_pipeline()
    if voice.switch_backend(backend):
        return {"status": "ok", "backend": backend}
    raise HTTPException(400, f"Backend {backend} not available or failed to start")


# Similarity monitor
@app.get("/similarity/status")
async def similarity_status():
    return {"status": "running"}


# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    
    # Send initial state
    state_mgr = get_state()
    await websocket.send_json({
        "type": "init",
        "data": state_mgr.get_state_snapshot()
    })
    
    try:
        while True:
            data = await websocket.receive_json()
            await handle_ws_message(websocket, data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


async def handle_ws_message(ws: WebSocket, data: Dict):
    msg_type = data.get("type")
    
    if msg_type == "command":
        ppt = get_ppt_controller()
        cmd = data.get("command", "").upper()
        target = data.get("target")
        
        loop = asyncio.get_event_loop()
        if cmd == "NEXT":
            result = await loop.run_in_executor(None, ppt.next_slide_sync)
        elif cmd == "PREVIOUS":
            result = await loop.run_in_executor(None, ppt.previous_slide_sync)
        elif cmd == "JUMP" and target:
            result = await loop.run_in_executor(None, ppt.jump_to_sync, target)
        elif cmd == "BLACKOUT":
            result = await loop.run_in_executor(None, ppt.toggle_blackout_sync)
        elif cmd == "FOCUS":
            result = await loop.run_in_executor(None, lambda: ppt.focus_window().result(timeout=5))
        else:
            result = {"success": False, "error": "Unknown command"}
        
        await ws.send_json({"type": "command_result", "command": cmd, "result": result})
    
    elif msg_type == "annotation":
        state_mgr = get_state()
        deck = state_mgr.active_deck
        if deck:
            from ppt_controller.core.state import AnnotationPoint, AnnotationStroke
            points = [AnnotationPoint(x=p["x"], y=p["y"], pressure=p.get("pressure", 1.0)) 
                     for p in data.get("points", [])]
            stroke = AnnotationStroke(
                id=data["stroke_id"],
                tool=data["tool"],
                color=data["color"],
                width=data["width"],
                points=points,
                slide_index=data["slide_index"],
                completed=data.get("completed", True)
            )
            state_mgr.add_annotation(state_mgr._active_deck_id, data["slide_index"], stroke)
            await ws.send_json({"type": "annotation_confirmed", "stroke_id": data["stroke_id"]})
    
    elif msg_type == "clear_annotations":
        state_mgr = get_state()
        state_mgr.clear_annotations(state_mgr._active_deck_id, data.get("slide_index"))
        await ws.send_json({"type": "annotations_cleared", "slide_index": data.get("slide_index")})
    
    elif msg_type == "undo_annotation":
        state_mgr = get_state()
        deck = state_mgr.active_deck
        if deck and data.get("slide_index") is not None:
            slide_idx = data["slide_index"]
            if 0 <= slide_idx < len(deck.slides):
                annotations = deck.slides[slide_idx].annotations
                if annotations:
                    removed = annotations.pop()
                    await manager.broadcast({
                        "type": "annotation_undone",
                        "data": {
                            "deck_id": state_mgr._active_deck_id,
                            "slide_index": slide_idx,
                            "stroke_id": removed.id
                        }
                    })
                    await ws.send_json({"type": "annotation_undone", "stroke_id": removed.id})
                else:
                    await ws.send_json({"type": "annotation_undone", "stroke_id": None, "error": "No annotations to undo"})
    
    elif msg_type == "request_annotations":
        state_mgr = get_state()
        deck = state_mgr.active_deck
        slide_idx = data.get("slide_index", 0)
        if deck and 0 <= slide_idx < len(deck.slides):
            strokes = deck.slides[slide_idx].annotations
            await ws.send_json({
                "type": "annotations_loaded",
                "data": {
                    "slide_index": slide_idx,
                    "strokes": [
                        {
                            "id": s.id,
                            "tool": s.tool,
                            "color": s.color,
                            "width": s.width,
                            "points": [{"x": p.x, "y": p.y, "pressure": p.pressure} for p in s.points],
                            "completed": s.completed
                        }
                        for s in strokes
                    ]
                }
            })
    
    elif msg_type == "request_deck_list":
        state_mgr = get_state()
        await ws.send_json({
            "type": "deck_list",
            "data": state_mgr.get_deck_list()
        })
    
    elif msg_type == "switch_deck":
        state_mgr = get_state()
        deck_id = data.get("deck_id")
        if deck_id and state_mgr.set_active_deck(deck_id):
            deck = state_mgr.active_deck
            await ws.send_json({
                "type": "deck_switched",
                "data": {
                    "deck_id": deck_id,
                    "slides": [
                        {"slideNumber": i+1, "url": s.thumbnail_path or ""} 
                        for i, s in enumerate(deck.slides)
                    ] if deck else [],
                    "current_slide": deck.current_slide + 1 if deck else 1
                }
            })
    
    elif msg_type == "ping":
        await ws.send_json({"type": "pong", "timestamp": time.time()})

    elif msg_type == "annotation_mode":
        # Just acknowledge - frontend handles local state
        pass


async def handle_auto_advance(slide: int, info: Dict):
    """Handle auto-advance from similarity monitor."""
    ppt = get_ppt_controller()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, ppt.jump_to_sync, slide)
    
    await manager.broadcast({
        "type": "auto_advance",
        "data": {"slide": slide, "info": info}
    })


# Static files for frontend
@app.get("/")
async def root():
    return FileResponse("public/index.html")


# Mount static files
app.mount("/static", StaticFiles(directory="public"), name="static")


if __name__ == "__main__":
    import uvicorn
    cfg = get_config()
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=cfg.network.python_backend_port,
        log_level=cfg.logging.level.lower()
    )
