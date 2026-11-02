"""
Main entry point for Presentation Controller.
Initializes all components and starts the application.
"""

import asyncio
import logging
import sys
import signal
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ppt_controller.config import load_config, get_config
from ppt_controller.api.server import app
from ppt_controller.core.ppt_com import get_ppt_controller
from ppt_controller.voice.pipeline import get_voice_pipeline
from ppt_controller.ai.similarity import get_similarity_monitor
from ppt_controller.annotations.manager import get_annotation_manager

# Configure logging
def setup_logging():
    cfg = get_config()
    log_dir = Path(cfg.paths.logs_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper()),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "ppt_controller.log", encoding="utf-8")
        ]
    )
    
    # Reduce noise from libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("grpc").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


async def main():
    """Main application entry point."""
    # Load configuration
    load_config()
    cfg = get_config()
    
    setup_logging()
    logger = logging.getLogger(__name__)
    
    logger.info(f"Starting {cfg.name} v{cfg.version}")
    logger.info(f"Backend: http://0.0.0.0:{cfg.network.python_backend_port}")
    logger.info(f"Frontend: http://0.0.0.0:{cfg.network.node_frontend_port}")
    
    # The FastAPI lifespan owns component startup and shutdown. Starting them here
    # as well created duplicate PowerPoint, voice, and similarity workers.
    logger.info("Starting API server; application components will initialize on startup...")
    
    # Import uvicorn here to avoid circular imports
    import uvicorn
    
    # Configure uvicorn
    uvicorn_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=cfg.network.python_backend_port,
        log_level=cfg.logging.level.lower(),
        access_log=False
    )
    server = uvicorn.Server(uvicorn_config)
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        server.should_exit = True
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run server
    try:
        await server.serve()
    finally:
        logger.info("Shutdown complete")


if __name__ == "__main__":
    # Windows event loop policy
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
