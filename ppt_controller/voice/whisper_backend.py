"""
Whisper.cpp Backend - Offline Speech Recognition using whisper.cpp.
"""

import threading
import queue
import subprocess
import time
import logging
import os
import json
from pathlib import Path
from typing import Optional, List
import urllib.request

from ppt_controller.config import get_config
from ppt_controller.voice.base import STTBackend, Transcript

logger = logging.getLogger(__name__)


class WhisperBackend(STTBackend):
    """Offline Whisper.cpp backend for speech recognition."""
    
    def __init__(self, config):
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._audio_buffer = bytearray()
        self._transcript_queue = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._feeder_thread: Optional[threading.Thread] = None
        self._model_path: Optional[Path] = None
        self._whisper_bin: Optional[Path] = None
        self._consecutive_errors = 0
        self._stderr_thread: Optional[threading.Thread] = None
    
    def start(self) -> bool:
        try:
            # Find whisper.cpp binary
            self._whisper_bin = self._find_whisper_binary()
            if not self._whisper_bin:
                logger.error("whisper.cpp binary not found. Please build whisper.cpp or download prebuilt binary.")
                return False
            
            # Get model
            self._model_path = self._get_model_path()
            if not self._model_path:
                logger.error("Whisper model not found")
                return False
            
            # Build command
            cfg = get_config()
            cmd = [
                str(self._whisper_bin),
                "-m", str(self._model_path),
                "-t", str(cfg.voice.whisper.threads),
                "-l", cfg.voice.whisper.language,
                "--step", "500",  # Process every 500ms
                "--length", "5000",  # 5 second context window
                "--vad-thold", str(cfg.voice.whisper.vad_threshold),
                "--output-json",
                "--no-timestamps",
                "-f", "-"  # Read from stdin
            ]
            
            # Add translate flag if needed
            if cfg.voice.whisper.translate:
                cmd.append("--translate")
            
            # Start process
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0  # Unbuffered
            )
            
            self._running = True
            
            # Start threads
            self._worker_thread = threading.Thread(target=self._read_output, name="Whisper-Output", daemon=True)
            self._worker_thread.start()
            
            self._feeder_thread = threading.Thread(target=self._feed_audio, name="Whisper-Feeder", daemon=True)
            self._feeder_thread.start()
            
            self._stderr_thread = threading.Thread(target=self._read_stderr, name="Whisper-Stderr", daemon=True)
            self._stderr_thread.start()
            
            logger.info(f"Whisper backend started with model: {cfg.voice.whisper.model}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Whisper backend: {e}")
            return False
    
    def stop(self):
        self._running = False
        
        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
            except:
                pass
            
            try:
                self._process.terminate()
                self._process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        
        for t in [self._worker_thread, self._feeder_thread, self._stderr_thread]:
            if t and t.is_alive():
                t.join(timeout=1.0)
        
        logger.info("Whisper backend stopped")
    
    def push_audio(self, audio_data: bytes):
        if self._running and audio_data:
            self._audio_buffer.extend(audio_data)
    
    def get_transcript(self, timeout: float = 0.1) -> Optional[Transcript]:
        try:
            return self._transcript_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    @property
    def is_running(self) -> bool:
        return self._running and self._process and self._process.poll() is None
    
    def _find_whisper_binary(self) -> Optional[Path]:
        """Find whisper.cpp binary in common locations."""
        # Check local build
        candidates = [
            Path("whisper.cpp/build/bin/whisper-stream.exe"),
            Path("whisper.cpp/build/bin/main.exe"),
            Path("whisper.cpp/build/bin/whisper-cli.exe"),
            Path("models/whisper/whisper-stream.exe"),
            Path("models/whisper/main.exe"),
            Path("models/whisper/whisper-cli.exe"),
        ]
        
        for c in candidates:
            if c.exists():
                return c
        
        # Check PATH
        for name in ["whisper-stream", "whisper-cli", "main"]:
            try:
                result = subprocess.run(
                    ["where" if os.name == "nt" else "which", name],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    path = Path(result.stdout.strip().split('\n')[0])
                    if path.exists():
                        return path
            except:
                pass
        
        return None
    
    def _get_model_path(self) -> Optional[Path]:
        """Get or download Whisper model."""
        cfg = get_config()
        model_name = cfg.voice.whisper.model
        model_dir = Path(cfg.paths.whisper_model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        
        model_file = model_dir / f"ggml-{model_name}.bin"
        
        if model_file.exists():
            return model_file
        
        # Download model
        logger.info(f"Downloading Whisper model: {model_name}")
        try:
            url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model_name}.bin"
            urllib.request.urlretrieve(url, model_file)
            logger.info(f"Model downloaded to {model_file}")
            return model_file
        except Exception as e:
            logger.error(f"Failed to download model: {e}")
            return None
    
    def _feed_audio(self):
        """Feed audio buffer to whisper stdin."""
        cfg = get_config()
        # 16kHz, 16-bit mono = 32000 bytes/sec
        # Feed in 100ms chunks = 3200 bytes
        chunk_size = 3200
        
        while self._running:
            if len(self._audio_buffer) >= chunk_size:
                chunk = bytes(self._audio_buffer[:chunk_size])
                self._audio_buffer = self._audio_buffer[chunk_size:]
                
                try:
                    self._process.stdin.write(chunk)
                    self._process.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    break
            else:
                time.sleep(0.05)
    
    def _read_output(self):
        """Read JSON transcripts from whisper stdout."""
        buffer = ""
        
        while self._running:
            try:
                line = self._process.stdout.readline()
                if not line:
                    break
                
                line = line.decode('utf-8', errors='ignore').strip()
                if not line:
                    continue
                
                # Accumulate JSON lines (whisper outputs one JSON per segment)
                buffer += line
                
                # Try to parse complete JSON
                try:
                    data = json.loads(buffer)
                    buffer = ""
                    
                    text = data.get("text", "").strip()
                    if text:
                        transcript = Transcript(
                            text=text,
                            is_final=True,  # Whisper outputs final segments
                            confidence=0.9
                        )
                        self._transcript_queue.put_nowait(transcript)
                        self._consecutive_errors = 0
                        
                except json.JSONDecodeError:
                    # Incomplete JSON, wait for more
                    if len(buffer) > 10000:
                        buffer = buffer[-5000:]  # Prevent buffer overflow
                        
            except Exception as e:
                logger.error(f"Whisper output read error: {e}")
                self._consecutive_errors += 1
                time.sleep(0.1)
    
    def _read_stderr(self):
        """Read and log stderr."""
        while self._running and self._process:
            try:
                line = self._process.stderr.readline()
                if not line:
                    break
                line = line.decode('utf-8', errors='ignore').strip()
                if line:
                    logger.debug(f"Whisper stderr: {line}")
            except:
                break
    
    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    @property
    def name(self) -> str:
        return "whisper"

    def process_audio(self, audio_data: bytes):
        self.push_audio(audio_data)

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "running": self.is_running,
            "errors": self.consecutive_errors
        }