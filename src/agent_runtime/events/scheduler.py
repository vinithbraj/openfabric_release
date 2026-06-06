"""Small in-process scheduler for Agent UI events."""

from __future__ import annotations

import threading
from collections.abc import Callable

from agent_runtime.events.models import AgentEventRecord
from agent_runtime.events.store import AgentEventStore, utc_now_iso


class AgentEventScheduler:
    """Poll the event store and launch due events through a callback."""

    def __init__(
        self,
        store: AgentEventStore,
        launch_callback: Callable[[AgentEventRecord, str], None],
        *,
        poll_seconds: float = 10.0,
        before_tick_callback: Callable[[], None] | None = None,
        enabled_callback: Callable[[], bool] | None = None,
    ) -> None:
        self.store = store
        self.launch_callback = launch_callback
        self.poll_seconds = max(1.0, float(poll_seconds or 10.0))
        self.before_tick_callback = before_tick_callback
        self.enabled_callback = enabled_callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start polling in a daemon thread."""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="agent-event-scheduler", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        """Stop the scheduler thread."""

        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout or 2.0)))

    def tick(self) -> int:
        """Claim and launch currently due events once."""

        if self.enabled_callback is not None and not self.enabled_callback():
            return 0
        launched = 0
        now = utc_now_iso()
        if self.before_tick_callback is not None:
            self.before_tick_callback()
        for event in self.store.claim_due_events(now=now):
            scheduled_for = event.next_run_at or now
            self.launch_callback(event, scheduled_for)
            launched += 1
        return launched

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                # Scheduled events are best-effort; one bad tick must not kill the
                # scheduler thread for all future events.
                pass
            self._stop_event.wait(self.poll_seconds)
