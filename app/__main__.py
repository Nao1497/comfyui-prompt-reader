"""Entry point: ``python -m app [path/to/config.toml]``."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn

from app.api import create_app
from app.config import ConfigError, load_config

APP_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    config_path = Path(argv[0]) if argv else APP_ROOT / "config.toml"
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
