"""XSOAR demistomock shim — fake runtime context for standalone integration execution."""

# ruff: noqa: N802, N803, N816

import contextvars
import logging
import uuid
from typing import Any

logger = logging.getLogger("dbot.demistomock")

# Module-level constants that some integrations check
is_debug = False
integrationContext: dict[str, Any] = {}


class DemistoMock:
    """Per-invocation XSOAR runtime mock. Thread-safe via contextvars."""

    __slots__ = (
        "_args",
        "_command",
        "_context",
        "_last_run",
        "_logs",
        "_params",
        "_results",
    )

    def __init__(self, command: str, args: dict[str, Any], params: dict[str, Any]) -> None:
        self._command = command
        self._args = args
        self._params = params
        self._results: list[Any] = []
        self._context: dict[str, Any] = {}
        self._last_run: dict[str, Any] = {}
        self._logs: list[tuple[str, str]] = []

    # ── Critical methods (every integration calls these) ──

    def command(self) -> str:
        return self._command

    def args(self) -> dict[str, Any]:
        return dict(self._args)

    def params(self) -> dict[str, Any]:
        return dict(self._params)

    def results(self, results: Any) -> None:
        """Capture output — this is the main interception point."""
        if isinstance(results, list):
            self._results.extend(results)
        else:
            self._results.append(results)

    def info(self, msg: str, *args: Any) -> None:
        logger.info(msg, *args)
        self._logs.append(("INFO", str(msg)))

    def debug(self, msg: str, *args: Any) -> None:
        logger.debug(msg, *args)
        self._logs.append(("DEBUG", str(msg)))

    def error(self, msg: str, *args: Any) -> None:
        logger.error(msg, *args)
        self._logs.append(("ERROR", str(msg)))

    def log(self, msg: str) -> None:
        self.debug(msg)

    # ── Important methods (many integrations use these) ──

    def getIntegrationContext(self) -> dict[str, Any]:
        return dict(self._context)

    def setIntegrationContext(self, context: dict[str, Any]) -> None:
        self._context = context

    def getLastRun(self) -> dict[str, Any]:
        return dict(self._last_run)

    def setLastRun(self, obj: dict[str, Any]) -> None:
        self._last_run = obj

    def incidents(self, incidents: Any = None) -> None:
        pass

    def createIncidents(self, incidents: list[Any], lastRun: Any = None, userID: Any = None) -> list[Any]:
        return incidents

    def credentials(self, credentials: Any) -> None:
        pass

    # ── Stubbed methods (rarely needed for HTTP integrations) ──

    def getFilePath(self, id: str) -> dict[str, str]:
        return {"path": "/tmp/file", "name": "file"}

    def investigation(self) -> dict[str, str]:
        return {"id": "0"}

    def incident(self) -> dict[str, Any]:
        return {}

    def executeCommand(self, command: str, args: dict[str, Any]) -> list[Any]:
        return []

    def uniqueFile(self) -> str:
        return str(uuid.uuid4())

    def context(self) -> dict[str, Any]:
        return {}

    def dt(self, obj: Any = None, trnsfrm: Any = None) -> Any:
        return obj

    def demistoUrls(self) -> dict[str, str]:
        return {}

    def updateModuleHealth(self, data: Any, is_error: bool = False) -> None:
        pass

    def addEntry(
        self,
        id: str,
        entry: Any,
        username: str | None = None,
        email: str | None = None,
        footer: str | None = None,
    ) -> None:
        pass

    def mirrorInvestigation(self, id: str, mirrorType: str, autoClose: bool = False) -> None:
        pass

    def directMessage(
        self,
        message: str,
        username: str | None = None,
        email: str | None = None,
        anyoneCanOpenIncidents: Any = None,
    ) -> None:
        pass

    def setContext(self, contextPath: str, value: Any) -> None:
        pass

    def getAllSupportedCommands(self) -> dict[str, Any]:
        return {}

    def get(self, obj: Any, field: str, defaultParam: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(field, defaultParam)
        return defaultParam

    def gets(self, obj: Any, field: str) -> Any:
        if isinstance(obj, dict) and field in obj:
            return obj[field]
        raise KeyError(f"Key '{field}' not found")

    def getAutoFocusApiKey(self) -> str:
        return ""

    def findUser(self, username: str | None = None, email: str | None = None) -> dict[str, Any]:
        return {}

    def handleEntitlementForUser(self, incidentID: str, guid: str, email: str, content: str, taskID: str) -> None:
        pass

    def createIndicators(self, indicators_batch: list[Any], noUpdate: bool = False) -> None:
        pass

    def searchIndicators(self, query: str | None = None, page: int = 0, size: int = 100) -> dict[str, Any]:
        return {"iocs": [], "total": 0}

    def getIndexHash(self) -> str:
        return ""

    def getLicenseID(self) -> str:
        return ""

    def getLicenseCustomField(self, key: str) -> str:
        return ""

    def calendarEntryToWar(self, entry: Any) -> None:
        pass

    def initialize(self) -> None:
        pass

    def getParam(self, param: str) -> Any:
        return self._params.get(param)

    def getArg(self, arg: str) -> Any:
        return self._args.get(arg)

    def setIntegrationContextVersioned(self, context: dict[str, Any], version: int = -1, sync: bool = False) -> None:
        pass

    def getIntegrationContextVersioned(self, refresh: bool = False) -> dict[str, Any]:
        return {"context": {}, "version": 0}

    # ── dbot-specific result retrieval (not part of XSOAR API) ──

    def get_results(self) -> list[Any]:
        return list(self._results)

    def get_logs(self) -> list[tuple[str, str]]:
        return list(self._logs)


# ── Context variable for thread-safe per-invocation isolation ──
_bootstrap_mock = DemistoMock(command="", args={}, params={})
_current_mock: contextvars.ContextVar[DemistoMock] = contextvars.ContextVar("demisto_mock", default=_bootstrap_mock)


def _set_mock(mock: DemistoMock) -> contextvars.Token[DemistoMock]:
    """Set the mock for the current execution context. Returns a reset token."""
    return _current_mock.set(mock)


def _get_mock() -> DemistoMock:
    """Get the mock for the current execution context."""
    return _current_mock.get()


def _reset_mock(token: contextvars.Token[DemistoMock]) -> None:
    """Reset the mock to the previous state."""
    _current_mock.reset(token)


# ── Module-level proxy ──
# Makes `import demistomock as demisto; demisto.params()` work
# by proxying attribute access to the current context's mock instance.


def __getattr__(name: str) -> Any:
    return getattr(_get_mock(), name)
