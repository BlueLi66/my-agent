import asyncio
import os
import signal
from types import SimpleNamespace

from my_agent.cli import app


def test_cli_prints_deltas_and_quits_cleanly(tmp_path, monkeypatch, capsys, anthropic_client, api_reply):
    client = anthropic_client((["Hel", "lo"], api_reply("Hello")))

    def create_client(**options):
        assert options["max_retries"] == 0
        return client

    monkeypatch.setattr(app, "AsyncAnthropic", create_client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_ID", "test-model")
    inputs = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    assert app.main(["-C", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert captured.out == "Hello\n"
    assert captured.err == ""
    assert client.closed
    assert [tool["name"] for tool in client.requests[0]["tools"]] == [
        "bash", "edit_file", "glob", "grep", "read_file", "write_file",
    ]


def test_cli_ctrl_c_closes_the_inflight_stream(tmp_path, monkeypatch, capsys, anthropic_client):
    async def interrupt():
        yield "partial"
        signal.raise_signal(signal.SIGINT)
        await asyncio.Future()

    client = anthropic_client((interrupt, None))
    monkeypatch.setattr(app, "AsyncAnthropic", lambda **options: client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_ID", "test-model")
    monkeypatch.setattr("builtins.input", lambda prompt: "ask")

    assert app.main(["-C", str(tmp_path)]) == 130

    captured = capsys.readouterr()
    assert captured.out == "partial\n"
    assert "Cancelled." in captured.err
    assert client.closed_streams == 1
    assert client.closed


def test_cli_registers_permission_and_requests_terminal_approval(tmp_path, monkeypatch, anthropic_client, api_reply):
    path = tmp_path / "approved.txt"
    path.touch()
    command = "del /q approved.txt" if os.name == "nt" else "rm approved.txt"
    client = anthropic_client(
        (["Removing"], api_reply("Removing", calls=[("id", "bash", {"command": command})])),
        (["Finished"], api_reply("Finished")),
    )
    monkeypatch.setattr(app, "AsyncAnthropic", lambda **options: client)
    monkeypatch.setattr(app.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_ID", "test-model")
    inputs = iter(["remove the test file", "y", "exit"])
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        return next(inputs)

    monkeypatch.setattr("builtins.input", read)
    assert app.main(["-C", str(tmp_path)]) == 0

    assert "[permission] bash" in prompts[1]
    assert command in prompts[1]
    assert not path.exists()
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is False
