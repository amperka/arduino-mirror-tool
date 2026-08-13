# region MODULE_CONTRACT
# PURPOSE: Convert process interruption signals into cooperative publication cancellation at safe operation boundaries.
# SCOPE:
# - SIGINT/SIGTERM installation, restoration, cancellation state, and operator exit status.
# - NOT: CLI parsing, publication orchestration, HTTP, or storage I/O.
# INVARIANTS: The first signal records cancellation and raises only from an active archive download; a second signal restores default handling for an emergency exit.
# KEYWORDS: signal, SIGINT, SIGTERM, cancellation, publication
# endregion MODULE_CONTRACT

"""Cooperative process-signal cancellation for publication commands."""

from __future__ import annotations

import signal
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import FrameType
    from typing import Any, Self

__all__ = ["PublicationCancelledError", "SignalCancellation"]


# region CLASS_PublicationCancelledError
# PURPOSE: End a CLI invocation with the conventional nonzero status for the signal that requested cancellation.
class PublicationCancelledError(RuntimeError):
    """A publication reached a safe boundary after an interruption signal."""

    def __init__(self, signal_number: int) -> None:
        """Record the received signal for reporting and process status."""
        self.signal_number = signal_number
        self.signal_name = signal.Signals(signal_number).name
        super().__init__(f"publication cancelled by {self.signal_name}")

    @property
    def exit_code(self) -> int:
        """Return the conventional shell exit status for the interruption signal."""
        return 128 + self.signal_number


# endregion CLASS_PublicationCancelledError


# region CLASS_SignalCancellation
# PURPOSE: Let the CLI defer SIGINT/SIGTERM handling until the publication reaches a boundary where no new operation has started.
class SignalCancellation:
    """Install temporary cooperative handlers for one publication invocation."""

    def __init__(self) -> None:
        """Initialize an invocation with no requested cancellation."""
        self._signal_number: int | None = None
        self._interrupting_download = False
        self._previous_handlers: dict[int, Any] = {}

    def __enter__(self) -> Self:
        """Install handlers and return this invocation's cancellation checker."""
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            self._previous_handlers[signal_number] = signal.signal(
                signal_number, self._request_cancellation
            )
        return self

    def __exit__(self, *_: object) -> None:
        """Restore the process handlers that were active before this invocation."""
        for signal_number, handler in self._previous_handlers.items():
            signal.signal(signal_number, handler)

    # region METHOD_check
    # PURPOSE: Stop the caller before its next external operation after SIGINT or SIGTERM was received.
    def check(self) -> None:
        """Raise the recorded cancellation at a cooperative operation boundary."""
        if self._signal_number is not None:
            raise PublicationCancelledError(self._signal_number)

    # endregion METHOD_check

    # region METHOD_interrupt_download
    # PURPOSE: Mark one archive transfer as safely interruptible so a signal can immediately unwind it and its temporary file cleanup.
    @contextmanager
    def interrupt_download(self) -> Generator[None]:
        """Make the enclosed archive download immediately interruptible by SIGINT or SIGTERM."""
        self.check()
        self._interrupting_download = True
        try:
            yield
        finally:
            self._interrupting_download = False

    # endregion METHOD_interrupt_download

    def _request_cancellation(self, signal_number: int, _: FrameType | None) -> None:
        """Record a first signal or use a second one for an emergency exit."""
        if self._signal_number is not None:
            signal.signal(signal_number, signal.SIG_DFL)
            signal.raise_signal(signal_number)
            return
        self._signal_number = signal_number
        if self._interrupting_download:
            raise PublicationCancelledError(self._signal_number)


# endregion CLASS_SignalCancellation
