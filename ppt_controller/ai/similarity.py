"""
AI Similarity Monitor - Advanced slide navigation using semantic analysis.
Uses embeddings for semantic matching + Nemotron VL for visual-language verification.
"""

import threading
import time
import logging
import json
import base64
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable
from dataclasses import dataclass, field
from collections import deque
import requests

from ppt_controller.config import get_config
from ppt_controller.core.state import get_state

logger = logging.getLogger(__name__)


@dataclass
class SlideEmbedding:
    slide_index: int
    text_embedding: List[float] = field(default_factory=list)
    image_embedding: List[float] = field(default_factory=list)
    thumbnail_hash: str = ""
    last_updated: float = 0


@dataclass
class SimilarityResult:
    slide_index: int
    score: float
    method: str  # "embedding", "nemotron", "combined"
    details: Dict[str, Any] = field(default_factory=dict)


class EmbeddingCache:
    """Cache for slide embeddings."""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: Dict[int, SlideEmbedding] = {}
        self._load_cache()
    
    def _load_cache(self):
        for f in self.cache_dir.glob("slide_*_embedding.json"):
            try:
                slide_idx = int(f.stem.split("_")[1])
                with open(f, "r") as fp:
                    data = json.load(fp)
                emb = SlideEmbedding(
                    slide_index=slide_idx,
                    text_embedding=data.get("text_embedding", []),
                    image_embedding=data.get("image_embedding", []),
                    thumbnail_hash=data.get("thumbnail_hash", ""),
                    last_updated=data.get("last_updated", 0)
                )
                self._memory_cache[slide_idx] = emb
            except Exception as e:
                logger.warning(f"Failed to load embedding cache {f}: {e}")
    
    def get(self, slide_index: int) -> Optional[SlideEmbedding]:
        return self._memory_cache.get(slide_index)
    
    def set(self, embedding: SlideEmbedding):
        self._memory_cache[embedding.slide_index] = embedding
        # Persist
        cache_file = self.cache_dir / f"slide_{embedding.slide_index}_embedding.json"
        try:
            with open(cache_file, "w") as f:
                json.dump({
                    "slide_index": embedding.slide_index,
                    "text_embedding": embedding.text_embedding,
                    "image_embedding": embedding.image_embedding,
                    "thumbnail_hash": embedding.thumbnail_hash,
                    "last_updated": embedding.last_updated
                }, f)
        except Exception as e:
            logger.warning(f"Failed to save embedding cache: {e}")
    
    def clear(self):
        self._memory_cache.clear()
        for f in self.cache_dir.glob("slide_*_embedding.json"):
            try:
                f.unlink()
            except:
                pass


class SimilarityMonitor:
    """Monitors speech and matches to slides using multiple methods."""
    
    def __init__(self):
        self.config = get_config()
        self.state = get_state()
        
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._speech_buffer = deque(maxlen=10)  # Last 10 transcripts
        self._current_slide = 0
        self._last_advance_time = 0
        self._min_advance_interval = 3.0  # Seconds between auto-advances
        
        # Embeddings
        self._embedding_cache = EmbeddingCache(Path("cache/embeddings"))
        self._slide_embeddings: Dict[int, SlideEmbedding] = {}
        
        # API endpoints
        self._nemotron_url = "https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-vl-1b-v2/reranking"
        self._embeddings_url = "https://ai.api.nvidia.com/v1/embeddings"
        
        # Thresholds
        self._embedding_threshold = self.config.ai.nemotron.similarity_threshold
        self._nemotron_threshold = self.config.ai.nemotron.similarity_threshold + 0.1
        self._combined_threshold = self.config.ai.nemotron.similarity_threshold
        
        # Callbacks
        self._on_advance: Optional[Callable[[int, Dict], None]] = None
    
    def start(self):
        """Start the similarity monitor."""
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, name="Similarity-Monitor", daemon=True)
        self._monitor_thread.start()
        logger.info("Similarity monitor started")
    
    def stop(self):
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
        logger.info("Similarity monitor stopped")
    
    def set_advance_callback(self, callback: Callable[[int, Dict], None]):
        """Called when monitor wants to advance slide."""
        self._on_advance = callback
    
    def add_transcript(self, text: str, is_final: bool):
        """Add transcript to buffer."""
        if len(text.strip()) < 3:
            return
        self._speech_buffer.append({
            "text": text,
            "is_final": is_final,
            "timestamp": time.time()
        })
    
    def update_slide_context(self, slide_index: int, slide_info: Dict):
        """Update current slide context."""
        self._current_slide = slide_index
    
    def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                self._check_similarity()
            except Exception as e:
                logger.error(f"Similarity check error: {e}")
            time.sleep(self.config.ai.nemotron.check_interval_sec)
    
    def _check_similarity(self):
        """Check if speech matches next slide."""
        # Need enough speech context
        full_text = " ".join([t["text"] for t in self._speech_buffer if t["is_final"]])
        if len(full_text.split()) < 10:
            return
        
        # Don't advance too frequently
        if time.time() - self._last_advance_time < self._min_advance_interval:
            return
        
        # Check next slide
        next_slide = self._current_slide + 1
        deck = self.state.active_deck
        if not deck or next_slide >= deck.total_slides:
            return
        
        # Get similarity scores
        results = []
        
        # Method 1: Embeddings (fast, semantic)
        if self.config.ai.embeddings.enabled:
            emb_score = self._check_embeddings(full_text, next_slide)
            if emb_score > 0:
                results.append(SimilarityResult(
                    slide_index=next_slide,
                    score=emb_score,
                    method="embedding"
                ))
        
        # Method 2: Nemotron VL (visual-language)
        if self.config.ai.nemotron.enabled:
            nemo_score = self._check_nemotron(full_text, next_slide)
            if nemo_score > 0:
                results.append(SimilarityResult(
                    slide_index=next_slide,
                    score=nemo_score,
                    method="nemotron"
                ))
        
        # Method 3: Combined
        if results:
            best = max(results, key=lambda r: r.score)
            if best.score >= self._combined_threshold:
                self._trigger_advance(next_slide, best)
    
    def _check_embeddings(self, text: str, slide_index: int) -> float:
        """Check semantic similarity using embeddings."""
        try:
            # Get or compute text embedding
            text_emb = self._get_text_embedding(text)
            if not text_emb:
                return 0.0
            
            # Get slide embedding
            slide_emb = self._get_slide_embedding(slide_index)
            if not slide_emb or not slide_emb.text_embedding:
                return 0.0
            
            # Cosine similarity
            return self._cosine_similarity(text_emb, slide_emb.text_embedding)
            
        except Exception as e:
            logger.error(f"Embedding check failed: {e}")
            return 0.0
    
    def _check_nemotron(self, text: str, slide_index: int) -> float:
        """Check visual-language similarity using Nemotron."""
        try:
            deck = self.state.active_deck
            if not deck:
                return 0.0
            
            # Get thumbnail
            thumb_path = deck.slides[slide_index].thumbnail_path if slide_index < len(deck.slides) else None
            if not thumb_path or not Path(thumb_path).exists():
                return 0.0
            
            # Use recent speech context (last ~500 chars)
            recent_text = text[-500:]
            
            # Encode image
            with open(thumb_path, "rb") as f:
                b64_image = base64.b64encode(f.read()).decode()
            
            payload = {
                "model": self.config.ai.nemotron.model,
                "query": {"text": recent_text},
                "passages": [{
                    "text": "Presentation slide content",
                    "image": f"data:image/png;base64,{b64_image}"
                }]
            }
            
            headers = {
                "Authorization": f"Bearer {self.config.ai.nemotron.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            
            response = requests.post(
                self._nemotron_url, 
                headers=headers, 
                json=payload, 
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'rankings' in data and data['rankings']:
                    score = data['rankings'][0].get('score', 
                            data['rankings'][0].get('relevance_score', 0))
                    return float(score)
            
            return 0.0
            
        except Exception as e:
            logger.error(f"Nemotron check failed: {e}")
            return 0.0
    
    def _get_text_embedding(self, text: str) -> Optional[List[float]]:
        """Get text embedding from API."""
        try:
            headers = {
                "Authorization": f"Bearer {self.config.ai.embeddings.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.config.ai.embeddings.model,
                "input": text,
                "encoding_format": "float"
            }
            
            response = requests.post(
                self._embeddings_url,
                headers=headers,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data and data['data']:
                    return data['data'][0]['embedding']
            
            return None
        except Exception as e:
            logger.error(f"Text embedding failed: {e}")
            return None
    
    def _get_slide_embedding(self, slide_index: int) -> Optional[SlideEmbedding]:
        """Get or compute slide embedding."""
        # Check memory cache
        if slide_index in self._slide_embeddings:
            return self._slide_embeddings[slide_index]
        
        # Check disk cache
        cached = self._embedding_cache.get(slide_index)
        if cached:
            self._slide_embeddings[slide_index] = cached
            return cached
        
        # Compute new embedding
        deck = self.state.active_deck
        if not deck or slide_index >= len(deck.slides):
            return None
        
        slide = deck.slides[slide_index]
        
        # Get text content (notes + title)
        slide_text = f"{slide.title}\n{slide.notes}"
        
        text_emb = self._get_text_embedding(slide_text)
        if not text_emb:
            return None
        
        # Get image embedding if thumbnail exists
        image_emb = []
        thumb_hash = ""
        if slide.thumbnail_path and Path(slide.thumbnail_path).exists():
            thumb_hash = self._file_hash(slide.thumbnail_path)
            # Could compute image embedding here if needed
        
        embedding = SlideEmbedding(
            slide_index=slide_index,
            text_embedding=text_emb,
            image_embedding=image_emb,
            thumbnail_hash=thumb_hash,
            last_updated=time.time()
        )
        
        self._slide_embeddings[slide_index] = embedding
        self._embedding_cache.set(embedding)
        
        return embedding
    
    def _file_hash(self, path: Path) -> str:
        """Quick file hash for cache invalidation."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0
        
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return dot / (norm_a * norm_b)
    
    def _trigger_advance(self, slide_index: int, result: SimilarityResult):
        """Trigger slide advance."""
        logger.info(f"Auto-advancing to slide {slide_index} (score: {result.score:.3f}, method: {result.method})")
        
        self._last_advance_time = time.time()
        self._speech_buffer.clear()  # Clear buffer after advance
        
        if self._on_advance:
            try:
                self._on_advance(slide_index, {
                    "score": result.score,
                    "method": result.method,
                    "details": result.details
                })
            except Exception as e:
                logger.error(f"Advance callback error: {e}")


# Global instance
_similarity_monitor: Optional[SimilarityMonitor] = None


def get_similarity_monitor() -> SimilarityMonitor:
    global _similarity_monitor
    if _similarity_monitor is None:
        _similarity_monitor = SimilarityMonitor()
    return _similarity_monitor