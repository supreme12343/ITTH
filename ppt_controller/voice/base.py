"""
Base classes for Speech-to-Text backends.
"""

from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod


@dataclass
class Transcript:
    """Transcript result from STT backend."""
    text: str
    is_final: bool
    confidence: float = 1.0
    language: str = "en"
    timestamp: float = 0.0


class STTBackend(ABC):
    """Abstract base class for STT backends."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Backend identifier."""
        pass
    
    @abstractmethod
    def start(self) -> bool:
        """Start the backend. Returns True if successful."""
        pass
    
    @abstractmethod
    def stop(self):
        """Stop the backend."""
        pass
    
    @abstractmethod
    def is_running(self) -> bool:
        """Check if backend is running."""
        pass
    
    @abstractmethod
    def process_audio(self, audio_data: bytes) -> Optional[Transcript]:
        """Process audio chunk and return transcript if available."""
        pass
    
    @abstractmethod
    def get_status(self) -> dict:
        """Get backend status."""
        pass