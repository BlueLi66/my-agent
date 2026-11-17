from pathlib import Path

import pytest

from my_agent.config import ConfigError, load_config


@pytest.fixture
def workspace_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".my-agent").mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in (
        "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "MODEL_ID", "FALLBACK_MODEL_ID", "GOAL_EVALUATOR_MODEL_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    return workspace, home / ".my-agent" / ".env"


def test_settings_layering_does_not_change_process_environment(workspace_config, monkeypatch):
    import os

    workspace, defaults = workspace_config
    defaults.write_text(
        "ANTHROPIC_API_KEY=test-key\nMODEL_ID=home-model\nFALLBACK_MODEL_ID=home-fallback\n",
        encoding="utf-8",
    )
    (workspace / ".env").write_text(
        "MODEL_ID=workspace-model\nANTHROPIC_BASE_URL=https://example.invalid/anthropic\n",
        encoding="utf-8-sig",
    )
    extra = workspace.parent / "extra.env"
    extra.write_text("MODEL_ID=explicit-model\nGOAL_EVALUATOR_MODEL_ID=judge\n", encoding="utf-8")
    assert load_config(workspace).model_id == "workspace-model"
    assert load_config(workspace, env_file=extra).model_id == "explicit-model"
    monkeypatch.setenv("MODEL_ID", "process-model")

    config = load_config(workspace, env_file=extra)
    assert config.model_id == "process-model"
    assert config.base_url == "https://example.invalid/anthropic"
    assert config.fallback_model_id == "home-fallback"
    assert config.goal_evaluator_model_id == "judge"
    assert config.api_key == "test-key"
    assert "test-key" not in repr(config)
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_missing_settings_have_actionable_errors(workspace_config):
    workspace, _ = workspace_config
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY, MODEL_ID"):
        load_config(workspace)
    with pytest.raises(ConfigError, match="Env file does not exist"):
        load_config(workspace, env_file=workspace / "invalid.env")
