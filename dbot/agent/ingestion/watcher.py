"""AlertWatcher — monitors a directory for new alert JSON files."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from dbot.agent.ingestion.cli import load_alert_from_file
from dbot.agent.models import Alert

logger = logging.getLogger("dbot.watcher")


class AlertWatcher:
    """Watches a directory for new *.json files.

    On file creation:
    - Parse as Alert → call async handler
    - Success: move to done/
    - Failure: move to failed/ with .error sidecar
    """

    def __init__(
        self,
        watch_dir: Path,
        handler: Callable[[Alert], Awaitable[None]],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._watch_dir = watch_dir
        self._handler = handler
        self._loop = loop
        self._done_dir = watch_dir / "done"
        self._failed_dir = watch_dir / "failed"
        self._observer: Observer | None = None

    def start(self) -> None:
        """Start watching the directory."""
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._done_dir.mkdir(exist_ok=True)
        self._failed_dir.mkdir(exist_ok=True)

        event_handler = _AlertFileHandler(
            handler=self._handler,
            done_dir=self._done_dir,
            failed_dir=self._failed_dir,
            loop=self._loop or asyncio.get_event_loop(),
        )
        self._observer = Observer()
        self._observer.schedule(event_handler, str(self._watch_dir), recursive=False)
        self._observer.start()
        logger.info("Watching %s for alert files", self._watch_dir)

    def stop(self) -> None:
        """Stop watching."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            logger.info("Watcher stopped")


class _AlertFileHandler(FileSystemEventHandler):
    """Handles new .json files in the watch directory."""

    def __init__(
        self,
        handler: Callable[[Alert], Awaitable[None]],
        done_dir: Path,
        failed_dir: Path,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._handler = handler
        self._done_dir = done_dir
        self._failed_dir = failed_dir
        self._loop = loop

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if not isinstance(event, FileCreatedEvent):
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".json":
            return
        logger.info("New alert file detected: %s", path.name)
        self._loop.call_soon_threadsafe(asyncio.ensure_future, self._process(path))

    async def _process(self, path: Path) -> None:
        try:
            alert = load_alert_from_file(path)
            await self._handler(alert)
            shutil.move(str(path), str(self._done_dir / path.name))
            logger.info("Alert processed: %s → done/", path.name)
        except Exception as exc:
            logger.exception("Failed to process alert %s: %s", path.name, exc)
            shutil.move(str(path), str(self._failed_dir / path.name))
            (self._failed_dir / f"{path.stem}.error").write_text(str(exc), encoding="utf-8")
