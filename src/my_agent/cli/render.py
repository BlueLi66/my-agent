from typing import TextIO

from my_agent.core.events import Event, ModelRetry, ModelStarted, SessionEnded, TextDelta, ToolStarted


class ConsoleRenderer:
    def __init__(self, output: TextIO) -> None:
        self._output = output
        self._on_line = False

    def __call__(self, event: Event) -> None:
        if isinstance(event, TextDelta) and event.delta:
            print(event.delta, end="", file=self._output, flush=True)
            self._on_line = not event.delta.endswith("\n")
        elif isinstance(event, ToolStarted):
            self.end_line()
            print(f"> {event.name}", file=self._output, flush=True)
        elif isinstance(event, ModelRetry):
            self.end_line()
            status = f" (HTTP {event.status_code})" if event.status_code is not None else ""
            print(f"Retrying model request in {event.delay_s:.1f}s{status}.", file=self._output, flush=True)
        elif isinstance(event, (ModelStarted, SessionEnded)):
            self.end_line()

    def end_line(self) -> None:
        if self._on_line:
            print(file=self._output, flush=True)
            self._on_line = False
