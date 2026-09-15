"""Local entrypoint. Run from the project root: python -m api.run"""

from __future__ import annotations

import sys

import uvicorn

from api.app import ConfigurationError, load_configuration


def main() -> int:
    try:
        config = load_configuration()
    except ConfigurationError as error:
        print(f"startup failed: {error}", file=sys.stderr)
        return 2
    uvicorn.run("api.app:app", host=config["host"], port=int(config["port"]), reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
