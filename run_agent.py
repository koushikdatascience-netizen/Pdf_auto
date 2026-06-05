"""Run the local FastAPI integration agent."""

from __future__ import annotations

import uvicorn

from integration_api.config import load_settings, validate_runtime_settings


def main() -> None:
    settings = load_settings()
    validate_runtime_settings(settings)
    uvicorn.run(
        "integration_api.main:create_app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=True,
        factory=True,
    )


if __name__ == "__main__":
    main()
