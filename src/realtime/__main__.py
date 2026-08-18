"""Start the M16 private live page and its read-only polling runtime."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.api.live_server import create_live_server

from .config import DEFAULT_CONFIG_PATH
from .runtime import create_runtime_from_env


DEFAULT_STATIC_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "m16"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local M16 QQQ Thermometer observer.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="versioned non-secret realtime config")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--static-root", default=str(DEFAULT_STATIC_ROOT))
    args = parser.parse_args()

    bundle = create_runtime_from_env(args.config)
    server = create_live_server(bundle.live_api, host=args.host, port=args.port, static_root=args.static_root)
    bundle.runtime.start()
    print(f"M16 live observer: http://{args.host}:{server.server_address[1]}/")
    print(f"refresh_interval_seconds={bundle.config.refresh_interval_seconds} display_timezone={bundle.config.display_timezone}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        bundle.runtime.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

