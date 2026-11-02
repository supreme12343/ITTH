"""
PowerPoint COM Controller - Thread-safe COM operations with async support.
Runs COM operations on dedicated STA thread to prevent GUI blocking.
"""

import threading
import time
import queue
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, Future
import sys

if sys.platform == "win32":
    import pythoncom
    import win32com.client
    import win32gui

from ppt_controller.config import get_config
from ppt_controller.core.state import get_state, SlideState, AnnotationStroke, SlideInfo

logger = logging.getLogger(__name__)


@dataclass
class COMCommand:
    action: str
    args: tuple = ()
    kwargs: dict = None
    future: Future = None
    
    def __post_init__(self):
        if self.kwargs is None:
            self.kwargs = {}
        if self.future is None:
            from concurrent.futures import Future
            self.future = Future()


class COMThread:
    """Dedicated STA thread for all COM operations."""
    
    def __init__(self):
        self._queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._initialized = False
        self._ppt_app = None
        self._active_presentation = None
        self._slide_show_window = None
        
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="COM-Thread", daemon=True)
        self._thread.start()
        # Wait for initialization
        while self._running and not self._initialized:
            time.sleep(0.01)
    
    def stop(self):
        self._running = False
        self._queue.put(None)  # Wake up thread
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
    
    def _run(self):
        """Main COM thread loop - runs on STA."""
        try:
            pythoncom.CoInitialize()
            self._initialized = True
            logger.info("COM thread initialized")
            
            while self._running:
                try:
                    # Pump waiting COM messages to keep RPC responsive
                    try:
                        pythoncom.PumpWaitingMessages()
                    except Exception:
                        pass

                    cmd = self._queue.get(timeout=0.1)
                    if cmd is None:
                        break
                    self._execute(cmd)
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"COM thread error: {e}")
                    if cmd and cmd.future:
                        cmd.future.set_exception(e)
        finally:
            try:
                pythoncom.CoUninitialize()
            except:
                pass
            logger.info("COM thread stopped")
    
    def _execute(self, cmd: COMCommand):
        """Execute a COM command."""
        try:
            method = getattr(self, f"_cmd_{cmd.action}", None)
            if method:
                result = method(*cmd.args, **cmd.kwargs)
                if cmd.future:
                    cmd.future.set_result(result)
            else:
                raise ValueError(f"Unknown COM command: {cmd.action}")
        except Exception as e:
            logger.error(f"COM command {cmd.action} failed: {e}")
            if cmd.future:
                cmd.future.set_exception(e)
    
    def submit(self, action: str, *args, **kwargs) -> Future:
        """Submit a command to the COM thread."""
        cmd = COMCommand(action=action, args=args, kwargs=kwargs)
        self._queue.put(cmd)
        return cmd.future
    
    # --- COM Commands ---
    
    def _cmd_connect(self) -> Dict[str, Any]:
        """Connect to running PowerPoint instance."""
        try:
            self._ppt_app = win32com.client.GetActiveObject("PowerPoint.Application")
            if self._ppt_app.Presentations.Count == 0:
                return {"connected": False, "error": "No presentation open"}
            
            self._active_presentation = self._ppt_app.ActivePresentation
            self._slide_show_window = None
            return {
                "connected": True,
                "presentation": self._active_presentation.Name,
                "slide_count": self._active_presentation.Slides.Count
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}
    
    def _cmd_get_state(self) -> Dict[str, Any]:
        """Get current presentation state."""
        if not self._active_presentation:
            conn = self._cmd_connect()
            if not conn.get("connected"):
                return conn
        
        try:
            pres = self._active_presentation
            total = pres.Slides.Count
            current = 0
            mode = "editing"
            
            try:
                if pres.SlideShowWindow:
                    self._slide_show_window = pres.SlideShowWindow
                    current = self._slide_show_window.View.CurrentShowPosition
                    mode = "live"
            except:
                pass
            
            return {
                "connected": True,
                "presentation": pres.Name,
                "total_slides": total,
                "current_slide": max(1, current),
                "mode": mode
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}
    
    def _cmd_next(self) -> Dict[str, Any]:
        """Go to next slide."""
        try:
            if self._slide_show_window:
                self._slide_show_window.View.Next()
                pos = self._slide_show_window.View.CurrentShowPosition
                return {"success": True, "slide": pos}
            return {"success": False, "error": "Not in slide show mode"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _cmd_previous(self) -> Dict[str, Any]:
        """Go to previous slide."""
        try:
            if self._slide_show_window:
                self._slide_show_window.View.Previous()
                pos = self._slide_show_window.View.CurrentShowPosition
                return {"success": True, "slide": pos}
            return {"success": False, "error": "Not in slide show mode"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _cmd_jump(self, slide_num: int) -> Dict[str, Any]:
        """Jump to specific slide."""
        try:
            if self._slide_show_window:
                self._slide_show_window.View.GotoSlide(slide_num)
                pos = self._slide_show_window.View.CurrentShowPosition
                return {"success": True, "slide": pos}
            return {"success": False, "error": "Not in slide show mode"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _cmd_blackout(self) -> Dict[str, Any]:
        """Toggle blackout."""
        try:
            if self._slide_show_window:
                state = self._slide_show_window.View.State
                self._slide_show_window.View.State = 3 if state != 3 else 1
                return {"success": True}
            return {"success": False, "error": "Not in slide show mode"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _cmd_laser(self) -> Dict[str, Any]:
        """Toggle laser pointer."""
        try:
            if self._slide_show_window:
                # Ctrl+L toggles laser
                import pyautogui
                pyautogui.hotkey('ctrl', 'l')
                return {"success": True}
            return {"success": False, "error": "Not in slide show mode"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _cmd_get_notes(self, slide_num: int) -> Dict[str, Any]:
        """Get speaker notes for slide."""
        try:
            if self._active_presentation and 1 <= slide_num <= self._active_presentation.Slides.Count:
                slide = self._active_presentation.Slides(slide_num)
                if slide.HasNotesPage:
                    notes = slide.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text.strip()
                    return {"success": True, "notes": notes}
            return {"success": False, "notes": ""}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _cmd_export_thumbnails(self, output_dir: str, format: str = "PNG") -> Dict[str, Any]:
        """Export all slides as thumbnails."""
        try:
            if not self._active_presentation:
                return {"success": False, "error": "No presentation"}
            
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            
            # Clear old thumbnails
            for f in output_path.glob("slide_*.png"):
                try:
                    f.unlink()
                except:
                    pass
            
            exported = 0
            for idx, slide in enumerate(self._active_presentation.Slides):
                slide_num = idx + 1
                target = output_path / f"slide_{slide_num:02d}.png"
                slide.Export(str(target), format)
                exported += 1
            
            return {"success": True, "exported": exported, "path": str(output_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _cmd_focus(self) -> Dict[str, Any]:
        """Focus PowerPoint window."""
        try:
            def enum_handler(hwnd, _):
                title = win32gui.GetWindowText(hwnd)
                if "PowerPoint Slide Show" in title:
                    win32gui.SetForegroundWindow(hwnd)
                    return False
                return True
            
            win32gui.EnumWindows(enum_handler, None)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


class PowerPointController:
    """High-level PowerPoint controller with async interface."""
    
    def __init__(self):
        self.config = get_config()
        self.com_thread = COMThread()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="COM-")
        self._callbacks: List[Callable] = []
    
    def start(self):
        """Start COM thread."""
        self.com_thread.start()
        logger.info("PowerPoint controller started")
    
    def stop(self):
        """Stop COM thread."""
        self.com_thread.stop()
        self._executor.shutdown(wait=True)
        logger.info("PowerPoint controller stopped")
    
    def add_state_callback(self, callback: Callable[[Dict], None]):
        """Add callback for state changes."""
        self._callbacks.append(callback)
    
    def _notify(self, state: Dict):
        for cb in self._callbacks:
            try:
                cb(state)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    # Async methods returning Futures
    
    def connect(self) -> Future:
        return self.com_thread.submit("connect")
    
    def get_state(self) -> Future:
        return self.com_thread.submit("get_state")
    
    def next_slide(self) -> Future:
        return self.com_thread.submit("next")
    
    def previous_slide(self) -> Future:
        return self.com_thread.submit("previous")
    
    def jump_to(self, slide_num: int) -> Future:
        return self.com_thread.submit("jump", slide_num)
    
    def toggle_blackout(self) -> Future:
        return self.com_thread.submit("blackout")
    
    def toggle_laser(self) -> Future:
        return self.com_thread.submit("laser")
    
    def get_notes(self, slide_num: int) -> Future:
        return self.com_thread.submit("get_notes", slide_num)
    
    def export_thumbnails(self, output_dir: str = None) -> Future:
        if output_dir is None:
            output_dir = str(self.config.paths.thumbnails_dir)
        return self.com_thread.submit("export_thumbnails", output_dir, self.config.powerpoint.thumbnail_format)
    
    def focus_window(self) -> Future:
        return self.com_thread.submit("focus")
    
    # Sync wrappers for simple use
    
    def connect_sync(self, timeout: float = 5.0) -> Dict:
        return self.connect().result(timeout=timeout)
    
    def get_state_sync(self, timeout: float = 5.0) -> Dict:
        return self.get_state().result(timeout=timeout)
    
    def next_slide_sync(self, timeout: float = 5.0) -> Dict:
        return self.next_slide().result(timeout=timeout)
    
    def previous_slide_sync(self, timeout: float = 5.0) -> Dict:
        return self.previous_slide().result(timeout=timeout)
    
    def jump_to_sync(self, slide_num: int, timeout: float = 5.0) -> Dict:
        return self.jump_to(slide_num).result(timeout=timeout)
    
    def toggle_blackout_sync(self, timeout: float = 5.0) -> Dict:
        return self.toggle_blackout().result(timeout=timeout)
    
    def export_thumbnails_sync(self, output_dir: str = None, timeout: float = 30.0) -> Dict:
        return self.export_thumbnails(output_dir).result(timeout=timeout)


# Global instance
_ppt_controller: Optional[PowerPointController] = None


def get_ppt_controller() -> PowerPointController:
    global _ppt_controller
    if _ppt_controller is None:
        _ppt_controller = PowerPointController()
    return _ppt_controller