"""Start the M17 unified personal portal."""

from __future__ import annotations

import argparse

from .config import DEFAULT_CONFIG_PATH, load_m17_config
from .gateway import create_m17_application
from .server import create_m17_server


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local M17 unified QQQ Thermometer portal.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="versioned non-secret M17 configuration")
    parser.add_argument("--host", default=None, help="optional localhost bind override")
    parser.add_argument("--port", type=int, default=None, help="optional localhost port override")
    args = parser.parse_args()
    config = load_m17_config(args.config)
    application = create_m17_application(config)
    server = create_m17_server(application, host=args.host, port=args.port)
    application.runtime_handle.start()
    print(f"M17 unified portal: http://{server.server_address[0]}:{server.server_address[1]}/")
    print(f"M16 refresh_interval_seconds={application.runtime_handle.realtime_config.refresh_interval_seconds} display_timezone={config.display_timezone}")
    print("paper_only=True execution_allowed=False order_created=False")
    if not application.runtime_handle.massive_key_configured:
        print("Massive API key is unavailable; the portal is serving a fail-closed read-only view")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        application.runtime_handle.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
