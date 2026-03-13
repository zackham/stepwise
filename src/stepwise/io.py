"""IOAdapter — unified terminal I/O for CLI and runner.

Sits outside the module DAG (imports nothing from stepwise). Can be imported from anywhere.
Three adapters: TerminalAdapter (rich + questionary), PlainAdapter (plain text), QuietAdapter (no-op).
"""

from __future__ import annotations

import abc
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator, TextIO


# ── Live flow handle ──────────────────────────────────────────────────


class LiveFlowHandle(abc.ABC):
    """Handle returned by live_flow() context manager."""

    @abc.abstractmethod
    def update_step(
        self,
        name: str,
        status: str,
        duration: float | None = None,
        cost: float | None = None,
        error: str | None = None,
    ) -> None: ...

    @abc.abstractmethod
    def update_summary(
        self,
        completed: int,
        total: int,
        cost: float | None = None,
        elapsed: float | None = None,
    ) -> None: ...

    @abc.abstractmethod
    def pause_for_input(self) -> None: ...

    @abc.abstractmethod
    def resume_after_input(self) -> None: ...


# ── IOAdapter ABC ─────────────────────────────────────────────────────


class IOAdapter(abc.ABC):
    """Abstract base for all CLI I/O."""

    # ── Output ────────────────────────────────────────────────────────

    @abc.abstractmethod
    def log(self, level: str, message: str) -> None:
        """Log a message. level: info, warn, error, success."""

    @abc.abstractmethod
    def banner(self, title: str, subtitle: str | None = None) -> None:
        """Display a banner (flow start, serve startup)."""

    @abc.abstractmethod
    def step_status(
        self,
        name: str,
        status: str,
        duration: float | None = None,
        cost: float | None = None,
        error: str | None = None,
    ) -> None:
        """Report a step status change."""

    @abc.abstractmethod
    def flow_complete(
        self,
        steps: int,
        duration: float,
        cost: float | None = None,
    ) -> None:
        """Report flow success."""

    @abc.abstractmethod
    def flow_failed(self, error: str | None = None) -> None:
        """Report flow failure."""

    @abc.abstractmethod
    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        """Display a table."""

    @abc.abstractmethod
    def note(self, content: str, title: str | None = None) -> None:
        """Display a boxed info panel."""

    # ── Live display ──────────────────────────────────────────────────

    @abc.abstractmethod
    @contextmanager
    def live_flow(
        self, flow_name: str, step_names: list[str],
    ) -> Generator[LiveFlowHandle, None, None]:
        """Context manager for live flow display."""
        yield  # type: ignore

    # ── Input ─────────────────────────────────────────────────────────

    @abc.abstractmethod
    def prompt_confirm(self, message: str, default: bool = True) -> bool: ...

    @abc.abstractmethod
    def prompt_select(self, message: str, choices: list[str]) -> str: ...

    @abc.abstractmethod
    def prompt_multi_select(self, message: str, choices: list[str]) -> list[str]: ...

    @abc.abstractmethod
    def prompt_text(
        self,
        message: str,
        default: str | None = None,
        multiline: bool = False,
    ) -> str: ...

    @abc.abstractmethod
    def prompt_number(
        self,
        message: str,
        min_val: float | None = None,
        max_val: float | None = None,
        default: float | None = None,
    ) -> float: ...

    # ── Concrete methods (shared by all adapters) ─────────────────────

    def collect_field(
        self, field_name: str, spec_dict: dict | None, is_only_field: bool,
    ) -> tuple[str, Any]:
        """Collect a single typed field. Returns (field_name, value).

        spec_dict is a raw dict (from OutputFieldSpec.to_dict()) to avoid
        importing models.py. Keys: type, required, default, description,
        options, multiple, min, max.
        """
        spec = spec_dict or {}
        field_type = spec.get("type", "str")
        required = spec.get("required", True)
        default = spec.get("default")
        description = spec.get("description")
        options = spec.get("options")
        multiple = spec.get("multiple", False)
        min_val = spec.get("min")
        max_val = spec.get("max")

        if description:
            self.log("info", f"  # {description}")

        if field_type == "bool":
            default_bool = default if isinstance(default, bool) else None
            result = self.prompt_confirm(
                field_name,
                default=default_bool if default_bool is not None else True,
            )
            return field_name, result

        if field_type == "number":
            result = self.prompt_number(
                field_name,
                min_val=min_val,
                max_val=max_val,
                default=default,
            )
            return field_name, result

        if field_type == "choice" and options:
            if multiple:
                result = self.prompt_multi_select(field_name, options)
                if not result and not required:
                    return field_name, None
                return field_name, result
            else:
                result = self.prompt_select(field_name, options)
                return field_name, result

        if field_type == "text":
            result = self.prompt_text(field_name, default=default, multiline=True)
            if not result and not required:
                return field_name, None
            if not result and default is not None:
                return field_name, default
            return field_name, result

        # Default: str
        result = self.prompt_text(field_name, default=default)
        if not result and not required:
            return field_name, None
        if not result and default is not None:
            return field_name, default
        return field_name, result

    def collect_human_input(
        self,
        prompt: str,
        fields: list[str],
        schema: dict[str, dict] | None = None,
    ) -> dict[str, Any]:
        """Collect all fields for a human step. Returns payload dict."""
        if prompt:
            self.note(prompt)

        payload: dict[str, Any] = {}
        is_only = len(fields) == 1

        for field_name in fields:
            spec = (schema or {}).get(field_name)
            _, value = self.collect_field(field_name, spec, is_only)
            if value is not None:
                payload[field_name] = value

        return payload


# ── PlainAdapter ──────────────────────────────────────────────────────


class _PlainLiveFlowHandle(LiveFlowHandle):
    """Line-by-line live flow handle for non-TTY environments."""

    def __init__(self, adapter: PlainAdapter):
        self._adapter = adapter

    def update_step(
        self,
        name: str,
        status: str,
        duration: float | None = None,
        cost: float | None = None,
        error: str | None = None,
    ) -> None:
        self._adapter.step_status(name, status, duration, cost, error)

    def update_summary(
        self, completed: int, total: int,
        cost: float | None = None, elapsed: float | None = None,
    ) -> None:
        pass  # no summary line in plain mode

    def pause_for_input(self) -> None:
        pass

    def resume_after_input(self) -> None:
        pass


class _NoopLiveFlowHandle(LiveFlowHandle):
    """No-op live flow handle for quiet mode."""

    def update_step(self, name: str, status: str, **kw: Any) -> None:
        pass

    def update_summary(self, completed: int, total: int, **kw: Any) -> None:
        pass

    def pause_for_input(self) -> None:
        pass

    def resume_after_input(self) -> None:
        pass


class PlainAdapter(IOAdapter):
    """Plain text adapter for non-TTY / piped output."""

    def __init__(
        self,
        output: TextIO | None = None,
        input_stream: TextIO | None = None,
    ):
        self._out = output or sys.stderr
        self._in = input_stream or sys.stdin

    def log(self, level: str, message: str) -> None:
        prefix = {
            "info": "  ",
            "warn": "⚠ ",
            "error": "✗ ",
            "success": "✓ ",
        }.get(level, "  ")
        self._out.write(f"{prefix}{message}\n")
        self._out.flush()

    def banner(self, title: str, subtitle: str | None = None) -> None:
        self._out.write(f"▸ {title}\n")
        if subtitle:
            self._out.write(f"  {subtitle}\n")
        self._out.write("\n")
        self._out.flush()

    def step_status(
        self,
        name: str,
        status: str,
        duration: float | None = None,
        cost: float | None = None,
        error: str | None = None,
    ) -> None:
        icons = {
            "running": "⠋",
            "completed": "✓",
            "failed": "✗",
            "suspended": "◆",
            "delegated": "↗",
            "waiting": "○",
        }
        icon = icons.get(status, "○")
        parts = [f"  {icon} {name:<16} {status}"]
        if duration is not None:
            parts.append(f"  {duration:.1f}s")
        if cost is not None:
            parts.append(f"${cost:.3f}")
        if error:
            parts.append(f"  {error}")
        self._out.write("".join(parts) + "\n")
        self._out.flush()

    def flow_complete(
        self, steps: int, duration: float, cost: float | None = None,
    ) -> None:
        parts = [f"{steps} steps", f"{duration:.1f}s"]
        if cost is not None:
            parts.append(f"${cost:.3f}")
        self._out.write(f"\n✓ Flow completed ({', '.join(parts)})\n")
        self._out.flush()

    def flow_failed(self, error: str | None = None) -> None:
        msg = "✗ Flow failed"
        if error:
            msg += f": {error}"
        self._out.write(f"\n{msg}\n")
        self._out.flush()

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        if not headers:
            return
        # Calculate column widths
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(str(cell)))

        # Header
        header_line = "  ".join(
            h.ljust(widths[i]) for i, h in enumerate(headers)
        )
        self._out.write(f"{header_line}\n")

        # Rows
        for row in rows:
            line = "  ".join(
                str(cell).ljust(widths[i]) if i < len(widths) else str(cell)
                for i, cell in enumerate(row)
            )
            self._out.write(f"{line}\n")
        self._out.flush()

    def note(self, content: str, title: str | None = None) -> None:
        self._out.write("\n")
        if title:
            self._out.write(f"  {title}\n")
        for line in content.strip().splitlines():
            self._out.write(f"  {line}\n")
        self._out.write("\n")
        self._out.flush()

    @contextmanager
    def live_flow(
        self, flow_name: str, step_names: list[str],
    ) -> Generator[LiveFlowHandle, None, None]:
        yield _PlainLiveFlowHandle(self)

    def prompt_confirm(self, message: str, default: bool = True) -> bool:
        default_str = " [Y/n]" if default else " [y/N]"
        self._out.write(f"? {message}{default_str} ")
        self._out.flush()
        raw = self._in.readline().strip().lower()
        if not raw:
            return default
        return raw in ("y", "yes", "true", "1")

    def prompt_select(self, message: str, choices: list[str]) -> str:
        self._out.write(f"? {message}\n")
        for i, choice in enumerate(choices, 1):
            self._out.write(f"  {i}. {choice}\n")
        self._out.write("  Choice: ")
        self._out.flush()
        raw = self._in.readline().strip()
        # Accept by number
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        # Accept by text
        if raw in choices:
            return raw
        # Fallback to first choice
        return choices[0] if choices else ""

    def prompt_multi_select(self, message: str, choices: list[str]) -> list[str]:
        self._out.write(f"? {message}\n")
        for i, choice in enumerate(choices, 1):
            self._out.write(f"  {i}. {choice}\n")
        self._out.write("  Toggle (1-N), blank to confirm: ")
        self._out.flush()

        selected: list[str] = []
        for _ in range(20):
            raw = self._in.readline().strip()
            if not raw:
                return selected
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(choices):
                    opt = choices[idx]
                    if opt in selected:
                        selected.remove(opt)
                    else:
                        selected.append(opt)
            except ValueError:
                if raw in choices:
                    if raw in selected:
                        selected.remove(raw)
                    else:
                        selected.append(raw)
            self._out.write(f"  Selected: {selected}. Toggle or blank: ")
            self._out.flush()
        return selected

    def prompt_text(
        self,
        message: str,
        default: str | None = None,
        multiline: bool = False,
    ) -> str:
        if multiline:
            self._out.write(f"? {message} (blank line to finish):\n")
            self._out.flush()
            lines: list[str] = []
            while True:
                line = self._in.readline()
                if not line:  # EOF
                    break
                stripped = line.rstrip("\n")
                if stripped == "":
                    break
                lines.append(stripped)
            return "\n".join(lines)
        else:
            default_str = f" [{default}]" if default else ""
            self._out.write(f"? {message}{default_str}: ")
            self._out.flush()
            raw = self._in.readline().strip()
            if not raw and default is not None:
                return default
            return raw

    def prompt_number(
        self,
        message: str,
        min_val: float | None = None,
        max_val: float | None = None,
        default: float | None = None,
    ) -> float:
        range_str = ""
        if min_val is not None and max_val is not None:
            range_str = f" ({min_val}-{max_val})"
        elif min_val is not None:
            range_str = f" (>={min_val})"
        elif max_val is not None:
            range_str = f" (<={max_val})"
        default_str = f" [{default}]" if default is not None else ""
        self._out.write(f"? {message}{range_str}{default_str}: ")
        self._out.flush()

        for _ in range(5):
            raw = self._in.readline().strip()
            if not raw and default is not None:
                return default
            try:
                num = float(raw)
                if min_val is not None and num < min_val:
                    self._out.write(f"  Must be >= {min_val}: ")
                    self._out.flush()
                    continue
                if max_val is not None and num > max_val:
                    self._out.write(f"  Must be <= {max_val}: ")
                    self._out.flush()
                    continue
                return num
            except ValueError:
                self._out.write("  Enter a number: ")
                self._out.flush()
        return default if default is not None else 0.0


# ── QuietAdapter ──────────────────────────────────────────────────────


class QuietAdapter(IOAdapter):
    """Suppresses all output. Input delegates to a PlainAdapter."""

    def __init__(self, input_stream: TextIO | None = None):
        self._plain = PlainAdapter(
            output=sys.stderr,
            input_stream=input_stream,
        )

    def log(self, level: str, message: str) -> None:
        pass

    def banner(self, title: str, subtitle: str | None = None) -> None:
        pass

    def step_status(self, name: str, status: str, **kw: Any) -> None:
        pass

    def flow_complete(self, steps: int, duration: float, cost: float | None = None) -> None:
        pass

    def flow_failed(self, error: str | None = None) -> None:
        pass

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        pass

    def note(self, content: str, title: str | None = None) -> None:
        pass

    @contextmanager
    def live_flow(
        self, flow_name: str, step_names: list[str],
    ) -> Generator[LiveFlowHandle, None, None]:
        yield _NoopLiveFlowHandle()

    def prompt_confirm(self, message: str, default: bool = True) -> bool:
        return self._plain.prompt_confirm(message, default)

    def prompt_select(self, message: str, choices: list[str]) -> str:
        return self._plain.prompt_select(message, choices)

    def prompt_multi_select(self, message: str, choices: list[str]) -> list[str]:
        return self._plain.prompt_multi_select(message, choices)

    def prompt_text(self, message: str, default: str | None = None, multiline: bool = False) -> str:
        return self._plain.prompt_text(message, default, multiline)

    def prompt_number(self, message: str, min_val: float | None = None, max_val: float | None = None, default: float | None = None) -> float:
        return self._plain.prompt_number(message, min_val, max_val, default)


# ── TerminalAdapter ───────────────────────────────────────────────────


# Braille spinner frames
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@dataclass
class _StepState:
    status: str = "waiting"
    start_time: float | None = None
    duration: float | None = None
    cost: float | None = None
    error: str | None = None


class _FlowDisplay:
    """Rich renderable for the live flow display."""

    def __init__(self, flow_name: str, step_names: list[str]):
        self.flow_name = flow_name
        self.step_names = step_names
        self.steps: dict[str, _StepState] = {
            name: _StepState() for name in step_names
        }
        self.completed = 0
        self.total = len(step_names)
        self.total_cost: float | None = None
        self.start_time = time.time()

    def __rich_console__(self, console, options):
        from rich.text import Text

        elapsed = time.time() - self.start_time
        spinner_idx = int(elapsed * 10) % len(_SPINNER_FRAMES)
        spinner = _SPINNER_FRAMES[spinner_idx]

        yield Text(f"  ▸ {self.flow_name}\n", style="bold")

        for name in self.step_names:
            state = self.steps[name]
            if state.status == "completed":
                icon = "✓"
                style = "green"
                info = "completed"
                if state.duration is not None:
                    info += f"   {state.duration:.1f}s"
                if state.cost is not None:
                    info += f"   ${state.cost:.3f}"
            elif state.status == "running":
                icon = spinner
                style = "cyan"
                run_time = time.time() - (state.start_time or time.time())
                info = f"running...  {run_time:.1f}s"
            elif state.status == "failed":
                icon = "✗"
                style = "red"
                info = "failed"
                if state.error:
                    info += f"     {state.error}"
            elif state.status == "suspended":
                icon = "◆"
                style = "yellow"
                info = "needs input"
            elif state.status == "delegated":
                icon = "↗"
                style = "blue"
                info = "delegated"
            else:
                icon = "○"
                style = "dim"
                info = "waiting"

            line = Text()
            line.append(f"  {icon} ", style=style)
            line.append(f"{name:<20}", style=style if state.status != "waiting" else "dim")
            line.append(f" {info}", style="dim" if state.status in ("waiting", "completed") else style)
            yield line

        # Summary line
        summary_parts = [f"{self.completed}/{self.total} steps"]
        if self.total_cost is not None:
            summary_parts.append(f"${self.total_cost:.3f}")
        summary_parts.append(f"{elapsed:.1f}s elapsed")
        yield Text()
        yield Text(f"  {' · '.join(summary_parts)}", style="dim")


class _TerminalLiveFlowHandle(LiveFlowHandle):
    """Live flow handle backed by rich.live.Live."""

    def __init__(self, display: _FlowDisplay, live, console):
        self._display = display
        self._live = live
        self._console = console
        self._pause_cursor_saved = False

    def update_step(
        self,
        name: str,
        status: str,
        duration: float | None = None,
        cost: float | None = None,
        error: str | None = None,
    ) -> None:
        if name in self._display.steps:
            state = self._display.steps[name]
            state.status = status
            if status == "running" and state.start_time is None:
                state.start_time = time.time()
            if duration is not None:
                state.duration = duration
            if cost is not None:
                state.cost = cost
            if error is not None:
                state.error = error

    def update_summary(
        self, completed: int, total: int,
        cost: float | None = None, elapsed: float | None = None,
    ) -> None:
        self._display.completed = completed
        self._display.total = total
        if cost is not None:
            self._display.total_cost = cost

    def pause_for_input(self) -> None:
        """Stop live display and save cursor position for cleanup."""
        self._live.stop()
        # Save cursor position so we can erase input prompts on resume
        file = self._console.file or sys.stderr
        file.write("\033[s")  # save cursor position
        file.flush()
        self._pause_cursor_saved = True

    def resume_after_input(self) -> None:
        """Erase input prompts and restart live display."""
        if self._pause_cursor_saved:
            file = self._console.file or sys.stderr
            file.write("\033[u")  # restore cursor position
            file.write("\033[J")  # clear from cursor to end of screen
            file.flush()
            self._pause_cursor_saved = False
        self._live.start()

    def print_final(self) -> None:
        """Print the final state of the display after live stops."""
        self._console.print(self._display)


class TerminalAdapter(IOAdapter):
    """Rich + questionary adapter for interactive TTY sessions."""

    def __init__(self, output: TextIO | None = None, input_stream: TextIO | None = None):
        from rich.console import Console
        self._console = Console(stderr=True, file=output)
        self._in = input_stream or sys.stdin
        # Fallback plain adapter for input when questionary is unavailable
        self._plain = PlainAdapter(output=output, input_stream=input_stream)

    def log(self, level: str, message: str) -> None:
        style_map = {
            "info": "",
            "warn": "yellow",
            "error": "bold red",
            "success": "green",
        }
        prefix = {
            "info": "  ",
            "warn": "⚠ ",
            "error": "✗ ",
            "success": "✓ ",
        }.get(level, "  ")
        style = style_map.get(level, "")
        self._console.print(f"{prefix}{message}", style=style, highlight=False)

    def banner(self, title: str, subtitle: str | None = None) -> None:
        from rich.text import Text
        self._console.print()
        self._console.print(Text(f"  ▸ {title}", style="bold"))
        if subtitle:
            self._console.print(Text(f"    {subtitle}", style="dim"))
        self._console.print()

    def step_status(
        self,
        name: str,
        status: str,
        duration: float | None = None,
        cost: float | None = None,
        error: str | None = None,
    ) -> None:
        from rich.text import Text

        icons = {
            "running": ("⠋", "cyan"),
            "completed": ("✓", "green"),
            "failed": ("✗", "red"),
            "suspended": ("◆", "yellow"),
            "delegated": ("↗", "blue"),
        }
        icon, style = icons.get(status, ("○", "dim"))
        line = Text()
        line.append(f"  {icon} ", style=style)
        line.append(f"{name:<16} ", style=style)
        line.append(status)
        if duration is not None:
            line.append(f"  {duration:.1f}s", style="dim")
        if cost is not None:
            line.append(f"  ${cost:.3f}", style="dim")
        if error:
            line.append(f"  {error}", style="red")
        self._console.print(line, highlight=False)

    def flow_complete(
        self, steps: int, duration: float, cost: float | None = None,
    ) -> None:
        parts = [f"{steps} steps", f"{duration:.1f}s"]
        if cost is not None:
            parts.append(f"${cost:.3f}")
        self._console.print()
        self._console.print(
            f"  ✓ Flow completed ({', '.join(parts)})",
            style="green",
            highlight=False,
        )

    def flow_failed(self, error: str | None = None) -> None:
        msg = "  ✗ Flow failed"
        if error:
            msg += f": {error}"
        self._console.print()
        self._console.print(msg, style="bold red", highlight=False)

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        from rich.table import Table

        t = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        for h in headers:
            t.add_column(h)
        for row in rows:
            t.add_row(*[str(c) for c in row])
        self._console.print(t)

    def note(self, content: str, title: str | None = None) -> None:
        from rich.panel import Panel
        self._console.print(
            Panel(content.strip(), title=title, border_style="dim", padding=(1, 2)),
        )

    @contextmanager
    def live_flow(
        self, flow_name: str, step_names: list[str],
    ) -> Generator[LiveFlowHandle, None, None]:
        from rich.live import Live

        display = _FlowDisplay(flow_name, step_names)
        live = Live(
            display,
            console=self._console,
            refresh_per_second=8,
            transient=True,
        )
        handle = _TerminalLiveFlowHandle(display, live, self._console)
        live.start()
        try:
            yield handle
        finally:
            live.stop()
            # Print the final state once (transient=True cleared the live display)
            handle.print_final()

    def prompt_confirm(self, message: str, default: bool = True) -> bool:
        try:
            import questionary
            return questionary.confirm(message, default=default).unsafe_ask()
        except (ImportError, KeyboardInterrupt):
            return self._plain.prompt_confirm(message, default)

    def prompt_select(self, message: str, choices: list[str]) -> str:
        try:
            import questionary
            return questionary.select(message, choices=choices).unsafe_ask()
        except (ImportError, KeyboardInterrupt):
            return self._plain.prompt_select(message, choices)

    def prompt_multi_select(self, message: str, choices: list[str]) -> list[str]:
        try:
            import questionary
            return questionary.checkbox(message, choices=choices).unsafe_ask()
        except (ImportError, KeyboardInterrupt):
            return self._plain.prompt_multi_select(message, choices)

    def prompt_text(
        self, message: str, default: str | None = None, multiline: bool = False,
    ) -> str:
        try:
            import questionary
            if multiline:
                # questionary doesn't have great multiline; use plain
                return self._plain.prompt_text(message, default, multiline=True)
            return questionary.text(message, default=default or "").unsafe_ask()
        except (ImportError, KeyboardInterrupt):
            return self._plain.prompt_text(message, default, multiline)

    def prompt_number(
        self,
        message: str,
        min_val: float | None = None,
        max_val: float | None = None,
        default: float | None = None,
    ) -> float:
        try:
            import questionary

            def validate(val: str) -> bool | str:
                if not val and default is not None:
                    return True
                try:
                    n = float(val)
                except ValueError:
                    return "Enter a number"
                if min_val is not None and n < min_val:
                    return f"Must be >= {min_val}"
                if max_val is not None and n > max_val:
                    return f"Must be <= {max_val}"
                return True

            result = questionary.text(
                message,
                default=str(default) if default is not None else "",
                validate=validate,
            ).unsafe_ask()
            if not result and default is not None:
                return default
            return float(result)
        except (ImportError, KeyboardInterrupt):
            return self._plain.prompt_number(message, min_val, max_val, default)


# ── Factory ───────────────────────────────────────────────────────────


def create_adapter(
    quiet: bool = False,
    force_plain: bool = False,
    output: TextIO | None = None,
    input_stream: TextIO | None = None,
) -> IOAdapter:
    """Create the appropriate IOAdapter.

    - quiet=True → QuietAdapter
    - force_plain=True or non-TTY → PlainAdapter
    - Otherwise → TerminalAdapter (with graceful fallback to PlainAdapter)
    """
    if quiet:
        return QuietAdapter(input_stream=input_stream)

    if force_plain:
        return PlainAdapter(output=output, input_stream=input_stream)

    # Check if stderr is a TTY
    check_stream = output or sys.stderr
    is_tty = hasattr(check_stream, "isatty") and check_stream.isatty()

    if not is_tty:
        return PlainAdapter(output=output, input_stream=input_stream)

    try:
        # Try to create a TerminalAdapter (requires rich)
        return TerminalAdapter(output=output, input_stream=input_stream)
    except ImportError:
        return PlainAdapter(output=output, input_stream=input_stream)
