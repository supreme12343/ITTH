"""
Configuration loader with YAML + environment variable support.
Priority: env var > config.yaml > defaults
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional, Union
from dataclasses import dataclass, field, asdict
import yaml
from dotenv import load_dotenv


@dataclass
class NetworkConfig:
    python_backend_port: int = 9005
    node_frontend_port: int = 8000
    udp_audio_port: int = 5005
    websocket_port: int = 9006
    use_http: bool = False


@dataclass
class PathsConfig:
    thumbnails_dir: str = "public/thumbnails"
    decks_dir: str = "decks"
    logs_dir: str = "logs"
    config_dir: str = "config"
    whisper_model_dir: str = "models/whisper"


@dataclass
class PowerPointConfig:
    com_timeout: float = 5.0
    thumbnail_format: str = "PNG"
    thumbnail_quality: int = 90
    max_slides_cache: int = 50
    auto_export_thumbnails: bool = True
    focus_on_navigate: bool = True


@dataclass
class RivaConfig:
    enabled: bool = True
    api_key: str = ""
    function_id: str = ""
    server: str = "grpc.nvcf.nvidia.com:443"
    sampling_rate: int = 16000
    language: str = "en-US"
    interim_results: bool = True


@dataclass
class WhisperConfig:
    enabled: bool = True
    model: str = "base.en"
    model_path: str = ""
    threads: int = 4
    language: str = "en"
    translate: bool = False
    vad_threshold: float = 0.5
    min_silence_ms: int = 500


@dataclass
class VoiceConfig:
    riva: RivaConfig = field(default_factory=RivaConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)


@dataclass
class GemmaConfig:
    enabled: bool = True
    api_key: str = ""
    url: str = "https://integrate.api.nvidia.com/v1/chat/completions"
    model: str = "google/gemma-3n-e2b-it"
    temperature: float = 0.1
    max_tokens: int = 50


@dataclass
class NemotronConfig:
    enabled: bool = True
    api_key: str = ""
    model: str = "nvidia/nemotron-3-ultra"
    similarity_threshold: float = 0.65
    check_interval_sec: float = 2.0
    buffer_size: int = 500


@dataclass
class EmbeddingsConfig:
    enabled: bool = True
    model: str = "nvidia/nv-embedqa-e5-v5"
    api_key: str = ""
    cache_embeddings: bool = True


@dataclass
class AIConfig:
    gemma: GemmaConfig = field(default_factory=GemmaConfig)
    nemotron: NemotronConfig = field(default_factory=NemotronConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)


@dataclass
class AnnotationsConfig:
    enabled: bool = True
    colors: list = field(default_factory=lambda: [
        "#FF3B30", "#34C759", "#007AFF", "#FF9F0A",
        "#AF52DE", "#FFCC00", "#FFFFFF", "#000000"
    ])
    default_color: str = "#FF3B30"
    default_width: int = 3
    save_with_deck: bool = True
    tools: list = field(default_factory=lambda: [
        "pen", "highlighter", "eraser", "laser", 
        "arrow", "rectangle", "circle", "text"
    ])


@dataclass
class MultiDeckConfig:
    enabled: bool = True
    auto_advance: bool = False
    transition_delay_sec: float = 1.0


@dataclass
class GUIConfig:
    theme: str = "dark"
    accent_color: str = "#6C63FF"
    font_family: str = "Inter, system-ui, sans-serif"
    show_console: bool = True
    console_max_lines: int = 1000
    auto_refresh_interval_ms: int = 500


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file_rotation: str = "10 MB"
    file_retention: str = "7 days"
    console_output: bool = True


@dataclass
class AppConfig:
    name: str = "Presentation Controller"
    version: str = "2.0.0"
    debug: bool = False
    network: NetworkConfig = field(default_factory=NetworkConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    powerpoint: PowerPointConfig = field(default_factory=PowerPointConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    annotations: AnnotationsConfig = field(default_factory=AnnotationsConfig)
    multi_deck: MultiDeckConfig = field(default_factory=MultiDeckConfig)
    gui: GUIConfig = field(default_factory=GUIConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int = 0) -> int:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _apply_env_overrides(config: AppConfig) -> AppConfig:
    """Apply environment variable overrides to config."""
    
    # Network
    config.network.python_backend_port = _env_int("PYTHON_BACKEND_PORT", config.network.python_backend_port)
    config.network.node_frontend_port = _env_int("NODE_FRONTEND_PORT", config.network.node_frontend_port)
    config.network.udp_audio_port = _env_int("UDP_AUDIO_PORT", config.network.udp_audio_port)
    config.network.websocket_port = _env_int("WEBSOCKET_PORT", config.network.websocket_port)
    config.network.use_http = _env_bool("USE_HTTP", config.network.use_http)
    
    # Voice - Riva
    config.voice.riva.api_key = _env_str("RIVA_API_KEY", config.voice.riva.api_key)
    config.voice.riva.function_id = _env_str("RIVA_FUNCTION_ID", config.voice.riva.function_id)
    config.voice.riva.server = _env_str("RIVA_SERVER", config.voice.riva.server)
    config.voice.riva.enabled = _env_bool("ENABLE_ONLINE_STT", config.voice.riva.enabled)
    
    # Voice - Whisper
    config.voice.whisper.enabled = _env_bool("ENABLE_OFFLINE_STT", config.voice.whisper.enabled)
    config.voice.whisper.model = _env_str("WHISPER_MODEL", config.voice.whisper.model)
    config.voice.whisper.threads = _env_int("WHISPER_THREADS", config.voice.whisper.threads)
    
    # AI - Gemma
    config.ai.gemma.api_key = _env_str("GEMMA_API_KEY", config.ai.gemma.api_key)
    config.ai.gemma.url = _env_str("GEMMA_URL", config.ai.gemma.url)
    config.ai.gemma.enabled = _env_bool("ENABLE_GEMMA", config.ai.gemma.enabled)
    
    # AI - Nemotron
    config.ai.nemotron.api_key = _env_str("NEMOTRON_API_KEY", config.ai.nemotron.api_key)
    config.ai.nemotron.enabled = _env_bool("ENABLE_SIMILARITY_MONITOR", config.ai.nemotron.enabled)
    config.ai.nemotron.similarity_threshold = _env_float("SIMILARITY_THRESHOLD", config.ai.nemotron.similarity_threshold)
    
    # AI - Embeddings
    config.ai.embeddings.api_key = _env_str("EMBEDDINGS_API_KEY", config.ai.embeddings.api_key)
    config.ai.embeddings.enabled = _env_bool("ENABLE_EMBEDDINGS", config.ai.embeddings.enabled)
    
    # Features
    config.annotations.enabled = _env_bool("ENABLE_ANNOTATIONS", config.annotations.enabled)
    config.multi_deck.enabled = _env_bool("ENABLE_MULTI_DECK", config.multi_deck.enabled)
    
    # GUI
    config.gui.theme = _env_str("THEME", config.gui.theme)
    config.gui.accent_color = _env_str("ACCENT_COLOR", config.gui.accent_color)
    config.gui.show_console = _env_bool("SHOW_CONSOLE", config.gui.show_console)
    
    # Debug
    config.debug = _env_bool("DEBUG", config.debug)
    config.logging.level = _env_str("LOG_LEVEL", config.logging.level)
    
    return config


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: Optional[Union[str, Path]] = None) -> AppConfig:
    """
    Load configuration from YAML file with environment variable overrides.
    
    Priority: .env > config.yaml > defaults
    """
    # Load .env first
    project_root = Path(__file__).parent.parent
    load_dotenv(project_root / ".env")
    
    # Start with defaults
    config = AppConfig()
    
    # Load from YAML if exists
    if config_path is None:
        config_path = project_root / "config.yaml"
    else:
        config_path = Path(config_path)
    
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}
        
        # Convert YAML dict to dataclass
        config = _dict_to_config(yaml_data, config)
    
    # Apply env overrides (highest priority)
    config = _apply_env_overrides(config)
    
    global _config_instance
    _config_instance = config
    return config


def _dict_to_config(data: Dict[str, Any], config: AppConfig) -> AppConfig:
    """Recursively populate config from dict."""
    from dataclasses import is_dataclass
    
    for key, value in data.items():
        if hasattr(config, key):
            attr = getattr(config, key)
            if is_dataclass(attr):
                # Nested dataclass
                setattr(config, key, _dict_to_config(value, attr))
            elif isinstance(attr, list) and value and isinstance(value[0], dict):
                # List of objects - keep as list
                setattr(config, key, value)
            elif isinstance(attr, dict) and isinstance(value, dict):
                # Dict
                setattr(config, key, value)
            else:
                # Simple value
                setattr(config, key, value)
    return config


def get_config() -> AppConfig:
    """Singleton accessor for loaded config."""
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance


def reload_config() -> AppConfig:
    """Force reload config from disk."""
    global _config_instance
    _config_instance = load_config()
    return _config_instance


# Global instance
_config_instance: Optional[AppConfig] = None


if __name__ == "__main__":
    cfg = load_config()
    print(f"App: {cfg.name} v{cfg.version}")
    print(f"Backend port: {cfg.network.python_backend_port}")
    print(f"Frontend port: {cfg.network.node_frontend_port}")
    print(f"Use HTTP: {cfg.network.use_http}")
    print(f"Riva enabled: {cfg.voice.riva.enabled}")
    print(f"Whisper model: {cfg.voice.whisper.model}")
    print(f"Gemma enabled: {cfg.ai.gemma.enabled}")
    print(f"Nemotron enabled: {cfg.ai.nemotron.enabled}")
    print(f"Annotations enabled: {cfg.annotations.enabled}")
