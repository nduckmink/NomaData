"""Data source connection config, loaded from a JSON file.

Kept separate from ``.env`` on purpose: connection definitions are config (often
a list, tweaked frequently), not environment/secrets. Edit ``data_sources.json``
at the repo root, or point ``NOMADATA_DATA_SOURCES_FILE`` elsewhere.

Secrets: put the password inline for local dev, or set ``password_env`` to the
name of an environment variable to read it from (keeps secrets out of the file).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field


class DataSourceConfig(BaseModel):
    name: str
    kind: str
    host: str = "localhost"
    port: int = 3306
    database: str
    user: str = ""
    password: str = ""
    # If set, read the password from this env var instead of `password`.
    password_env: str | None = None

    def resolve_password(self) -> str:
        if self.password_env:
            return os.environ.get(self.password_env, "")
        return self.password


class DataSourcesFile(BaseModel):
    sources: list[DataSourceConfig] = Field(default_factory=list)


def _default_file() -> Path:
    """Locate ``data_sources.json`` at the repo root (marker-based)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() or (parent / "pnpm-workspace.yaml").exists():
            return parent / "data_sources.json"
    return Path("data_sources.json")


def load_data_sources(path: str | None = None) -> list[DataSourceConfig]:
    """Load configured data sources; returns [] if the file is absent."""
    file_path = Path(path) if path else _default_file()
    if not file_path.is_file():
        return []
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    return DataSourcesFile.model_validate(raw).sources
