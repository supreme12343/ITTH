"""
Riva ASR Backend - NVIDIA Riva Streaming Speech Recognition.
"""

import threading
import queue
import logging
import time
from typing import Optional, Generator
import grpc

from ppt_controller.config import get_config
from ppt_controller.voice.base import STTBackend, Transcript

logger = logging.getLogger(__name__)


class RivaBackend(STTBackend):
    """NVIDIA Riva Streaming ASR backend."""
    
    def __init__(self, config):
        self.config = config
        self._client = None
        self._running = False
        self._audio_queue = queue.Queue(maxsize=200)
        self._transcript_queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._grpc_channel = None
        self._auth_metadata = None
        self._consecutive_errors = 0
    
    def start(self) -> bool:
        try:
            cfg = get_config()
            
            api_key = cfg.voice.riva.api_key
            function_id = cfg.voice.riva.function_id
            server = cfg.voice.riva.server
            
            if not api_key or not function_id:
                logger.warning("Riva API key or Function ID not configured")
                return False
            
            # Setup gRPC auth metadata
            self._auth_metadata = [
                ("function-id", function_id),
                ("authorization", f"Bearer {api_key}")
            ]
            
            # Create secure channel
            auth = grpc.ssl_channel_credentials()
            call_creds = grpc.metadata_call_credentials(
                lambda _, callback: callback(self._auth_metadata, None)
            )
            composite = grpc.composite_channel_credentials(auth, call_creds)
            
            self._grpc_channel = grpc.secure_channel(server, composite)
            
            # Import Riva client (lazy)
            import riva.client
            self._client = riva.client.ASRService(self._grpc_channel)
            
            # Configure recognition
            riva_config = riva.client.RecognitionConfig(
                encoding=riva.client.AudioEncoding.LINEAR_PCM,
                sample_rate_hertz=cfg.voice.riva.sampling_rate,
                language_code=cfg.voice.riva.language,
                max_alternatives=1,
                enable_automatic_punctuation=True,
                verbatim_transcripts=False,
            )
            self._streaming_config = riva.client.StreamingRecognitionConfig(
                config=riva_config,
                interim_results=cfg.voice.riva.interim_results
            )
            
            self._running = True
            self._worker_thread = threading.Thread(target=self._process_stream, name="Riva-Worker", daemon=True)
            self._worker_thread.start()
            
            logger.info("Riva backend started successfully")
            return True
            
        except ImportError:
            logger.error("riva.client not installed. Install with: pip install nvidia-riva-client")
            return False
        except Exception as e:
            logger.error(f"Failed to start Riva backend: {e}")
            return False
    
    def stop(self):
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=3.0)
        if self._grpc_channel:
            self._grpc_channel.close()
        logger.info("Riva backend stopped")
    
    def push_audio(self, audio_data: bytes):
        if self._running:
            try:
                self._audio_queue.put_nowait(audio_data)
            except queue.Full:
                # Drop oldest if full
                try:
                    self._audio_queue.get_nowait()
                    self._audio_queue.put_nowait(audio_data)
                except queue.Empty:
                    pass
    
    def get_transcript(self, timeout: float = 0.1) -> Optional[Transcript]:
        try:
            return self._transcript_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    def _audio_generator(self) -> Generator[bytes, None, None]:
        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.5)
                yield chunk
            except queue.Empty:
                continue
    
    def _process_stream(self):
        try:
            responses = self._client.streaming_response_generator(
                self._audio_generator(), self._streaming_config
            )
            
            for response in responses:
                if not self._running:
                    break
                
                for result in response.results:
                    if not result.alternatives:
                        continue
                    
                    alt = result.alternatives[0]
                    transcript = Transcript(
                        text=alt.transcript,
                        is_final=result.is_final,
                        confidence=alt.confidence if hasattr(alt, 'confidence') else 1.0
                    )
                    
                    try:
                        self._transcript_queue.put_nowait(transcript)
                        self._consecutive_errors = 0
                    except queue.Full:
                        pass
                        
        except grpc.RpcError as e:
            logger.error(f"Riva gRPC error: {e}")
            self._consecutive_errors += 1
        except Exception as e:
            logger.error(f"Riva processing error: {e}")
            self._consecutive_errors += 1
        finally:
            self._running = False
    
    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    @property
    def name(self) -> str:
        return "riva"

    def process_audio(self, audio_data: bytes):
        self.push_audio(audio_data)

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "running": self.is_running,
            "errors": self.consecutive_errors
        }