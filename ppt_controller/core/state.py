"""
Core state management and event bus for Presentation Controller.
Thread-safe state with observer pattern.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any, Set
from enum import Enum
from collections import defaultdict
import queue


class SlideState(Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    LIVE = "live"
    EDITING = "editing"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class CommandType(Enum):
    NEXT = "NEXT"
    PREVIOUS = "PREVIOUS"
    JUMP = "JUMP"
    BLACKOUT = "BLACKOUT"
    LASER = "LASER"
    ANNOTATE_START = "ANNOTATE_START"
    ANNOTATE_UPDATE = "ANNOTATE_UPDATE"
    ANNOTATE_END = "ANNOTATE_END"
    ANNOTATE_CLEAR = "ANNOTATE_CLEAR"
    SYNC_REQUEST = "SYNC_REQUEST"
    DECK_SWITCH = "DECK_SWITCH"
    THUMBNAIL_READY = "THUMBNAIL_READY"


@dataclass
class AnnotationPoint:
    x: float
    y: float
    pressure: float = 1.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class AnnotationStroke:
    id: str
    tool: str
    color: str
    width: int
    points: List[AnnotationPoint] = field(default_factory=list)
    slide_index: int = 0
    completed: bool = False
    created_at: float = field(default_factory=time.time)


@dataclass
class SlideInfo:
    index: int
    title: str = ""
    notes: str = ""
    thumbnail_path: str = ""
    annotations: List[AnnotationStroke] = field(default_factory=list)


@dataclass
class DeckState:
    name: str
    path: str
    slides: List[SlideInfo] = field(default_factory=list)
    current_slide: int = 0
    total_slides: int = 0
    is_active: bool = False
    last_modified: float = field(default_factory=time.time)


class EventBus:
    """Thread-safe pub/sub event bus."""
    
    def __init__(self):
        self._subscribers: Dict[str, Set[Callable]] = defaultdict(set)
        self._lock = threading.RLock()
    
    def subscribe(self, event_type: str, callback: Callable) -> Callable:
        with self._lock:
            self._subscribers[event_type].add(callback)
        return lambda: self.unsubscribe(event_type, callback)
    
    def unsubscribe(self, event_type: str, callback: Callable):
        with self._lock:
            self._subscribers[event_type].discard(callback)
    
    def emit(self, event_type: str, data: Any = None):
        with self._lock:
            subscribers = self._subscribers.get(event_type, set()).copy()
        
        for callback in subscribers:
            try:
                callback(data)
            except Exception as e:
                print(f"[EventBus] Error in subscriber for {event_type}: {e}")


class StateManager:
    """Central state management with thread-safe access."""
    
    def __init__(self):
        self._lock = threading.RLock()
        self._decks: Dict[str, DeckState] = {}
        self._active_deck_id: Optional[str] = None
        self._connection_state = SlideState.DISCONNECTED
        self._last_error: Optional[str] = None
        self._command_queue = queue.Queue()
        self._events = EventBus()
        
        # Callbacks for external systems
        self.on_deck_change: Optional[Callable] = None
        self.on_slide_change: Optional[Callable] = None
        self.on_annotation_added: Optional[Callable] = None
        self.on_state_change: Optional[Callable] = None
    
    @property
    def events(self) -> EventBus:
        return self._events
    
    @property
    def connection_state(self) -> SlideState:
        with self._lock:
            return self._connection_state
    
    @connection_state.setter
    def connection_state(self, state: SlideState):
        with self._lock:
            if self._connection_state != state:
                self._connection_state = state
                self._events.emit("connection_state_changed", state.value)
                if self.on_state_change:
                    self.on_state_change(state)
    
    @property
    def active_deck(self) -> Optional[DeckState]:
        with self._lock:
            if self._active_deck_id and self._active_deck_id in self._decks:
                return self._decks[self._active_deck_id]
            return None
    
    @property
    def current_slide(self) -> int:
        deck = self.active_deck
        return deck.current_slide if deck else 0
    
    @property
    def total_slides(self) -> int:
        deck = self.active_deck
        return deck.total_slides if deck else 0
    
    def set_active_deck(self, deck_id: str) -> bool:
        with self._lock:
            if deck_id in self._decks:
                if self._active_deck_id:
                    self._decks[self._active_deck_id].is_active = False
                self._active_deck_id = deck_id
                self._decks[deck_id].is_active = True
                self._events.emit("deck_changed", deck_id)
                if self.on_deck_change:
                    self.on_deck_change(deck_id)
                return True
            return False
    
    def add_deck(self, deck: DeckState) -> str:
        with self._lock:
            deck_id = f"deck_{len(self._decks)}"
            self._decks[deck_id] = deck
            self._events.emit("deck_added", deck_id)
            if not self._active_deck_id:
                self.set_active_deck(deck_id)
            return deck_id
    
    def remove_deck(self, deck_id: str) -> bool:
        with self._lock:
            if deck_id in self._decks:
                was_active = deck_id == self._active_deck_id
                del self._decks[deck_id]
                if was_active:
                    self._active_deck_id = next(iter(self._decks)) if self._decks else None
                self._events.emit("deck_removed", deck_id)
                return True
            return False
    
    def update_slide(self, slide_index: int):
        with self._lock:
            deck = self.active_deck
            if deck and 0 <= slide_index < deck.total_slides:
                old_slide = deck.current_slide
                deck.current_slide = slide_index
                self._events.emit("slide_changed", {
                    "old": old_slide,
                    "new": slide_index,
                    "deck": self._active_deck_id
                })
                if self.on_slide_change:
                    self.on_slide_change(old_slide, slide_index)
    
    def set_slides(self, deck_id: str, slides: List[SlideInfo]):
        with self._lock:
            if deck_id in self._decks:
                self._decks[deck_id].slides = slides
                self._decks[deck_id].total_slides = len(slides)
                self._events.emit("slides_updated", deck_id)
    
    def add_annotation(self, deck_id: str, slide_index: int, stroke: AnnotationStroke):
        with self._lock:
            if deck_id in self._decks and 0 <= slide_index < len(self._decks[deck_id].slides):
                self._decks[deck_id].slides[slide_index].annotations.append(stroke)
                self._events.emit("annotation_added", {
                    "deck_id": deck_id,
                    "slide_index": slide_index,
                    "stroke": stroke
                })
                if self.on_annotation_added:
                    # Schedule async callback
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.on_annotation_added(deck_id, slide_index, stroke))
                    except RuntimeError:
                        # No running loop, call sync
                        pass
    
    def clear_annotations(self, deck_id: str, slide_index: int = None):
        with self._lock:
            if deck_id in self._decks:
                deck = self._decks[deck_id]
                if slide_index is not None and 0 <= slide_index < len(deck.slides):
                    deck.slides[slide_index].annotations.clear()
                else:
                    for slide in deck.slides:
                        slide.annotations.clear()
                self._events.emit("annotations_cleared", {"deck_id": deck_id, "slide_index": slide_index})
    
    def queue_command(self, command: Dict[str, Any]):
        self._command_queue.put(command)
    
    def get_next_command(self, timeout: float = 0.1) -> Optional[Dict]:
        try:
            return self._command_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def get_deck_list(self) -> List[Dict]:
        with self._lock:
            return [
                {
                    "id": did,
                    "name": deck.name,
                    "path": deck.path,
                    "current_slide": deck.current_slide,
                    "total_slides": deck.total_slides,
                    "is_active": deck.is_active
                }
                for did, deck in self._decks.items()
            ]
    
    def get_state_snapshot(self) -> Dict:
        with self._lock:
            deck = self.active_deck
            return {
                "connection_state": self._connection_state.value,
                "active_deck": self._active_deck_id,
                "decks": self.get_deck_list(),
                "current_slide": deck.current_slide if deck else 0,
                "total_slides": deck.total_slides if deck else 0,
                "slide_title": deck.slides[deck.current_slide].title if deck and deck.slides else "",
                "slide_notes": deck.slides[deck.current_slide].notes if deck and deck.slides else "",
                "last_error": self._last_error,
                "timestamp": time.time()
            }


# Global state instance
_state: Optional[StateManager] = None


def get_state() -> StateManager:
    global _state
    if _state is None:
        _state = StateManager()
    return _state