import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


class ConfigError(ValueError):
    """Required model configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    model_id: str
    base_url: str | None = None
    fallback_model_id: str | None = None
    goal_evaluator_model_id: str | None = None


def load_config(workdir: Path, *, env_file: Path | None = None) -> Config:
    """Load user defaults, workspace settings, an explicit file, and process overrides."""
    files = [Path.home() / ".my-agent" / ".env", workdir / ".env"]
    if env_file is not None:
        if not env_file.is_file():
            raise ConfigError(f"Env file does not exist: {env_file}")
        files.append(env_file)

    values: dict[str, str | None] = {}
    for path in files:
        if path.is_file():
            values.update(dotenv_values(path, encoding="utf-8-sig"))

    def read(key: str) -> str | None:
        return (os.environ.get(key, values.get(key)) or "").strip() or None

    api_key = read("ANTHROPIC_API_KEY")
    model_id = read("MODEL_ID")
    if not api_key or not model_id:
        missing = [name for name, value in (("ANTHROPIC_API_KEY", api_key), ("MODEL_ID", model_id)) if not value]
        raise ConfigError(f"Missing model configuration: {', '.join(missing)}")

    return Config(
        api_key=api_key,
        model_id=model_id,
        base_url=read("ANTHROPIC_BASE_URL"),
        fallback_model_id=read("FALLBACK_MODEL_ID"),
        goal_evaluator_model_id=read("GOAL_EVALUATOR_MODEL_ID"),
    )
