"""
Voice Pipeline - Main coordinator for speech-to-text backends.
"""

import threading
import logging
import time
from typing import Optional, Callable, List, Dict, Any
from dataclasses import dataclass

from ppt_controller.config import get_config
from ppt_controller.voice.riva_backend import RivaBackend
from ppt_controller.voice.whisper_backend import WhisperBackend
from ppt_controller.voice.base import STTBackend, Transcript

logger = logging.getLogger(__name__)


@dataclass
class VoiceConfig:
    """Voice pipeline configuration."""
    preferred_backend: str = "riva"  # "riva" or "whisper"
    fallback_enabled: bool = True
    auto_switch_on_error: bool = True
    error_threshold: int = 5  # Switch after N consecutive errors


class VoicePipeline:
    """Unified voice pipeline managing multiple STT backends."""
    
    def __init__(self):
        self.config = get_config()
        self._pipeline_config = VoiceConfig(
            preferred_backend="riva" if self.config.voice.riva.enabled else "whisper",
            fallback_enabled=True,
            auto_switch_on_error=True
        )
        
        self._backends: Dict[str, STTBackend] = {}
        self._active_backend: Optional[str] = None
        self._active_backend_obj: Optional[STTBackend] = None
        self._running = False
        
        # Audio input
        self._udp_socket = None
        self._udp_thread: Optional[threading.Thread] = None
        
        # Transcript processing
        self._transcript_callbacks: List[Callable[[Transcript], None]] = []
        self._error_count = 0
        self._last_transcript_time = 0
        
        # UDP audio config
        self._udp_port = self.config.network.udp_audio_port
        self._udp_host = "0.0.0.0"
        
        # Initialize available backends
        self._init_backends()
    
    def _init_backends(self):
        """Initialize all configured backends."""
        if self.config.voice.riva.enabled:
            self._backends["riva"] = RivaBackend(self.config)
            logger.info("Riva backend initialized")
        
        if self.config.voice.whisper.enabled:
            self._backends["whisper"] = WhisperBackend(self.config)
            logger.info("Whisper backend initialized")
    
    def start(self) -> bool:
        """Start the voice pipeline with best available backend."""
        if self._running:
            return True
        
        # Try preferred backend first
        preferred = self._pipeline_config.preferred_backend
        if preferred in self._backends:
            if self._backends[preferred].start():
                self._active_backend = preferred
                self._active_backend_obj = self._backends[preferred]
                logger.info(f"Voice pipeline started with {preferred} backend")
                self._start_audio_capture()
                self._running = True
                return True
        
        # Fallback to other backends
        if self._pipeline_config.fallback_enabled:
            for name, backend in self._backends.items():
                if name != preferred:
                    if backend.start():
                        self._active_backend = name
                        self._active_backend_obj = backend
                        logger.info(f"Voice pipeline started with fallback {name} backend")
                        self._start_audio_capture()
                        self._running = True
                        return True
        
        logger.error("No STT backend could start")
        return False
    
    def stop(self):
        """Stop the voice pipeline."""
        self._running = False
        
        if self._udp_socket:
            self._udp_socket.close()
        if self._udp_thread:
            self._udp_thread.join(timeout=1.0)
        
        for backend in self._backends.values():
            try:
                backend.stop()
            except Exception as e:
                logger.error(f"Error stopping backend: {e}")
        
        self._active_backend = None
        self._active_backend_obj = None
        logger.info("Voice pipeline stopped")
    
    def _start_audio_capture(self):
        """Start UDP audio capture."""
        import socket
        
        self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_socket.bind((self._udp_host, self._udp_port))
        self._udp_socket.settimeout(0.5)
        
        self._udp_thread = threading.Thread(target=self._udp_receiver, name="UDP-Audio", daemon=True)
        self._udp_thread.start()
        
        logger.info(f"UDP audio capture started on {self._udp_host}:{self._udp_port}")
    
    def _udp_receiver(self):
        """Receive audio from UDP and feed to active backend."""
        while self._running and self._udp_socket:
            try:
                data, _ = self._udp_socket.recvfrom(16384)
                if data and self._active_backend_obj:
                    self._active_backend_obj.push_audio(data)
            except TimeoutError:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"UDP receive error: {e}")
    
    def add_transcript_callback(self, callback: Callable[[Transcript], None]):
        """Add callback for transcript events."""
        self._transcript_callbacks.append(callback)
    
    def process_transcripts(self):
        """Process transcripts from active backend (call periodically)."""
        if not self._active_backend_obj:
            return
        
        while True:
            transcript = self._active_backend_obj.get_transcript(timeout=0.0)
            if not transcript:
                break
            
            self._last_transcript_time = time.time()
            self._error_count = 0  # Reset on success
            
            # Call all callbacks
            for callback in self._transcript_callbacks:
                try:
                    callback(transcript)
                except Exception as e:
                    logger.error(f"Transcript callback error: {e}")
    
    def switch_backend(self, backend_name: str) -> bool:
        """Switch to a different STT backend."""
        if backend_name not in self._backends:
            logger.error(f"Backend {backend_name} not available")
            return False
        
        if backend_name == self._active_backend:
            return True
        
        # Stop current
        if self._active_backend_obj:
            self._active_backend_obj.stop()
        
        # Start new
        new_backend = self._backends[backend_name]
        if new_backend.start():
            self._active_backend = backend_name
            self._active_backend_obj = new_backend
            logger.info(f"Switched to {backend_name} backend")
            return True
        else:
            # Try to restart old
            if self._active_backend and self._backends[self._active_backend].start():
                self._active_backend_obj = self._backends[self._active_backend]
            return False
    
    @property
    def active_backend(self) -> Optional[str]:
        return self._active_backend
    
    @property
    def is_running(self) -> bool:
        return self._running and self._active_backend_obj and self._active_backend_obj.is_running
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "active_backend": self._active_backend,
            "backends": {name: b.is_running for name, b in self._backends.items()},
            "last_transcript_ago": time.time() - self._last_transcript_time if self._last_transcript_time else None
        }


# Global instance
_voice_pipeline: Optional[VoicePipeline] = None


def get_voice_pipeline() -> VoicePipeline:
    global _voice_pipeline
    if _voice_pipeline is None:
        _voice_pipeline = VoicePipeline()
    return _voice_pipeline