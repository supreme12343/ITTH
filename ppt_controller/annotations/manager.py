"""
Annotations Module - Canvas-based slide annotations with multiple tools.
"""

import asyncio
import json
import time
import uuid
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

from ppt_controller.config import get_config
from ppt_controller.core.state import get_state, AnnotationStroke, AnnotationPoint


class AnnotationTool(Enum):
    PEN = "pen"
    HIGHLIGHTER = "highlighter"
    ERASER = "eraser"
    LASER = "laser"
    ARROW = "arrow"
    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    TEXT = "text"


@dataclass
class AnnotationLayer:
    """Annotation layer for a slide."""
    slide_index: int
    strokes: List[AnnotationStroke] = field(default_factory=list)
    visible: bool = True
    locked: bool = False


class AnnotationManager:
    """Manages annotations across slides and decks."""
    
    def __init__(self):
        self.config = get_config()
        self.state = get_state()
        self._layers: Dict[str, Dict[int, AnnotationLayer]] = {}  # deck_id -> slide_index -> layer
        self._current_stroke: Optional[AnnotationStroke] = None
        self._current_deck_id: Optional[str] = None
        self._current_tool = AnnotationTool.PEN
        self._current_color = self.config.annotations.default_color
        self._current_width = self.config.annotations.default_width
        
        # Subscribe to state changes
        self.state.events.subscribe("deck_changed", self._on_deck_change)
        self.state.events.subscribe("slide_changed", self._on_slide_change)
    
    def _on_deck_change(self, deck_id: str):
        self._current_deck_id = deck_id
        if deck_id not in self._layers:
            self._layers[deck_id] = {}
    
    def _on_slide_change(self, old_slide: int, new_slide: int):
        # Auto-save current stroke if any
        if self._current_stroke and not self._current_stroke.completed:
            self._finalize_current_stroke()
    
    def set_tool(self, tool: str):
        if tool in [t.value for t in AnnotationTool]:
            self._current_tool = AnnotationTool(tool)
    
    def set_color(self, color: str):
        if color in self.config.annotations.colors:
            self._current_color = color
    
    def set_width(self, width: int):
        self._current_width = max(1, min(20, width))
    
    def start_stroke(self, slide_index: int, x: float, y: float, pressure: float = 1.0) -> str:
        """Start a new annotation stroke."""
        stroke_id = str(uuid.uuid4())[:8]
        
        point = AnnotationPoint(x=x, y=y, pressure=pressure)
        
        self._current_stroke = AnnotationStroke(
            id=stroke_id,
            tool=self._current_tool.value,
            color=self._current_color,
            width=self._current_width,
            points=[point],
            slide_index=slide_index
        )
        
        return stroke_id
    
    def update_stroke(self, stroke_id: str, x: float, y: float, pressure: float = 1.0):
        """Add point to current stroke."""
        if self._current_stroke and self._current_stroke.id == stroke_id:
            point = AnnotationPoint(x=x, y=y, pressure=pressure)
            self._current_stroke.points.append(point)
    
    def end_stroke(self, stroke_id: str):
        """Finalize current stroke."""
        if self._current_stroke and self._current_stroke.id == stroke_id:
            self._finalize_current_stroke()
    
    def _finalize_current_stroke(self):
        if not self._current_stroke:
            return
        
        if len(self._current_stroke.points) < 2:
            # Too short, discard
            self._current_stroke = None
            return
        
        self._current_stroke.completed = True
        
        # Save to state
        if self._current_deck_id:
            self.state.add_annotation(
                self._current_deck_id,
                self._current_stroke.slide_index,
                self._current_stroke
            )
        
        # Also save to local layer
        if self._current_deck_id and self._current_deck_id in self._layers:
            layer = self._layers[self._current_deck_id].setdefault(
                self._current_stroke.slide_index, 
                AnnotationLayer(slide_index=self._current_stroke.slide_index)
            )
            layer.strokes.append(self._current_stroke)
        
        self._current_stroke = None
    
    def cancel_stroke(self):
        """Cancel current stroke without saving."""
        self._current_stroke = None
    
    def clear_slide(self, deck_id: str, slide_index: int):
        """Clear all annotations on a slide."""
        if deck_id in self._layers and slide_index in self._layers[deck_id]:
            self._layers[deck_id][slide_index].strokes.clear()
        self.state.clear_annotations(deck_id, slide_index)
    
    def clear_all(self, deck_id: str):
        """Clear all annotations in a deck."""
        if deck_id in self._layers:
            self._layers[deck_id].clear()
        self.state.clear_annotations(deck_id)
    
    def get_slide_annotations(self, deck_id: str, slide_index: int) -> List[AnnotationStroke]:
        """Get annotations for a slide."""
        deck = self.state.active_deck
        if deck and slide_index < len(deck.slides):
            return deck.slides[slide_index].annotations
        return []
    
    def get_all_annotations(self, deck_id: str) -> Dict[int, List[AnnotationStroke]]:
        """Get all annotations for a deck organized by slide."""
        result = {}
        deck = self.state.active_deck
        if deck:
            for i, slide in enumerate(deck.slides):
                if slide.annotations:
                    result[i] = slide.annotations
        return result
    
    def export_annotations(self, deck_id: str) -> Dict:
        """Export annotations to JSON-serializable format."""
        return self.get_all_annotations(deck_id)
    
    def import_annotations(self, deck_id: str, data: Dict):
        """Import annotations from exported format."""
        self.clear_all(deck_id)
        
        for slide_idx_str, strokes_data in data.items():
            slide_idx = int(slide_idx_str)
            for stroke_data in strokes_data:
                points = [
                    AnnotationPoint(x=p["x"], y=p["y"], pressure=p.get("pressure", 1.0))
                    for p in stroke_data.get("points", [])
                ]
                stroke = AnnotationStroke(
                    id=stroke_data["id"],
                    tool=stroke_data["tool"],
                    color=stroke_data["color"],
                    width=stroke_data["width"],
                    points=points,
                    slide_index=slide_idx,
                    completed=True,
                    created_at=stroke_data.get("created_at", time.time())
                )
                self.state.add_annotation(deck_id, slide_idx, stroke)
    
    def undo_last(self, deck_id: str, slide_index: int) -> bool:
        """Remove the last annotation stroke on a slide."""
        annotations = self.get_slide_annotations(deck_id, slide_index)
        if annotations:
            last = annotations[-1]
            # Remove from state (rebuild list without last)
            deck = self.state.active_deck
            if deck and slide_index < len(deck.slides):
                deck.slides[slide_index].annotations = annotations[:-1]
                self.state.events.emit("annotation_removed", {
                    "deck_id": deck_id,
                    "slide_index": slide_index,
                    "stroke_id": last.id
                })
                return True
        return False


# Global instance
_annotation_manager: Optional[AnnotationManager] = None


def get_annotation_manager() -> AnnotationManager:
    global _annotation_manager
    if _annotation_manager is None:
        _annotation_manager = AnnotationManager()
    return _annotation_manager