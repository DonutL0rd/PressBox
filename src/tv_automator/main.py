"""TV-Automator entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn


def setup_logging(data_dir: Path) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "tv-automator.log"),
            logging.StreamHandler(sys.stderr),
        ],
    )

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    import os
    data_dir = Path(os.getenv("DATA_DIR", "/data"))
    setup_logging(data_dir)

    log = logging.getLogger(__name__)
    log.info("Starting TV-Automator web server on port 5000...")

    from tv_automator.web.app import app

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=5000,
        log_level="info",
    )


if __name__ == "__main__":
    main()
