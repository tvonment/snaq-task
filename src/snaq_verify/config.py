"""Runtime configuration loaded from environment variables.

Keeps credential and endpoint handling in one place so the rest of the
codebase never touches ``os.environ`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Environment-derived settings for the verification run."""

    azure_endpoint: str
    azure_api_key: str
    azure_deployment: str
    usda_api_key: str
    max_concurrent: int
    log_level: str

    @classmethod
    def load(cls) -> Settings:
        """Load settings from ``.env`` and the process environment."""
        load_dotenv(override=False)

        def required(key: str) -> str:
            value = os.environ.get(key)
            if not value:
                raise RuntimeError(
                    f"Missing required environment variable: {key}. "
                    "See .env.example."
                )
            return value

        return cls(
            azure_endpoint=required("AZURE_OPENAI_ENDPOINT"),
            azure_api_key=required("AZURE_OPENAI_API_KEY"),
            azure_deployment=required("AZURE_OPENAI_DEPLOYMENT"),
            usda_api_key=required("USDA_API_KEY"),
            max_concurrent=int(os.environ.get("MAX_CONCURRENT_VERIFICATIONS", "5")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
