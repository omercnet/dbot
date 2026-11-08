"""XSOAR `demisto` runtime for one integration command inside a sandbox container.

Stdlib only, Python 3.10 compatible. The runner calls `_init()` once, then the integration
reads its command/args/params from here and its output is captured by `results()`.
"""

# ruff: noqa: N802, N803, N816, PLW0603, S108
import uuid
from typing import Any

MAX_LOGS = 500
MAX_LOG_LEN = 4000

is_debug = False
integrationContext: dict[str, Any] = {}
callingContext: dict[str, Any] = {}

_command = ""
_args: dict[str, Any] = {}
_params: dict[str, Any] = {}
_results: list[Any] = []
_logs: list[str] = []
_last_run: dict[str, Any] = {}
_brand = ""


def _init(command: str, args: dict[str, Any], params: dict[str, Any], brand: str) -> None:
    """Set up the invocation. Not part of the XSOAR API."""
    global _command, _args, _params, _brand
    _command = command
    _args = dict(args)
    # Drop None values so `params().get(key, default)` falls back to the integration's default.
    _params = {k: v for k, v in params.items() if v is not None}
    _brand = brand
    callingContext.clear()
    callingContext.update(
        {
            "context": {
                "IntegrationBrand": brand,
                "IntegrationInstance": instance_name(),
                "ParentEntry": {},
                "ExecutedCommands": [],
            },
            "integration": True,
            "command": command,
            "args": dict(args),
        }
    )


def instance_name() -> str:
    return (_brand or "integration") + "_instance_1"


def _log(level: str, msg: Any, args: tuple[Any, ...]) -> None:
    if len(_logs) >= MAX_LOGS:
        return
    text = str(msg)
    if args:
        try:
            text = text % args
        except (TypeError, ValueError):
            text = text + " " + " ".join(str(a) for a in args)
    _logs.append(level + ": " + text[:MAX_LOG_LEN])


# Invocation


def command() -> str:
    return _command


def args() -> dict[str, Any]:
    return dict(_args)


def params() -> dict[str, Any]:
    return dict(_params)


def getArg(arg: str) -> Any:
    return _args.get(arg)


def getParam(param: str) -> Any:
    return _params.get(param)


def results(results: Any) -> None:
    if isinstance(results, list):
        _results.extend(results)
    else:
        _results.append(results)


def credentials(credentials: Any) -> None:
    return None


def initialize() -> None:
    return None


# Logging


def info(msg: Any, *args: Any) -> None:
    _log("INFO", msg, args)


def debug(msg: Any, *args: Any) -> None:
    _log("DEBUG", msg, args)


def error(msg: Any, *args: Any) -> None:
    _log("ERROR", msg, args)


def log(msg: Any) -> None:
    _log("DEBUG", msg, ())


# Server and instance metadata


def demistoVersion() -> dict[str, Any]:
    return {"version": "8.9.0", "buildNumber": "1234567", "platform": "xsoar", "engine": False}


def integrationInstance() -> str:
    return instance_name()


def getLicenseID() -> str:
    return "dbot"


def getLicenseCustomField(key: str) -> str:
    return ""


def demistoUrls() -> dict[str, str]:
    return {"server": "https://localhost", "investigation": "https://localhost/#/Details/1"}


def getAutoFocusApiKey() -> str:
    return ""


def getIndexHash() -> str:
    return ""


def getAllSupportedCommands() -> dict[str, Any]:
    return {}


def getModules() -> dict[str, Any]:
    return {}


def isTimeSensitive() -> bool:
    return False


# Incident and investigation


def incident() -> dict[str, Any]:
    return {"id": "1", "name": "dbot", "labels": [], "CustomFields": {}}


def incidents(incidents: Any = None) -> Any:
    if incidents is None:
        return [incident()]
    return None


def get_incidents() -> list[Any]:
    return [incident()]


def investigation() -> dict[str, Any]:
    return {"id": "1"}


def parentEntry() -> dict[str, Any]:
    return {}


def context() -> dict[str, Any]:
    return {}


def setContext(contextPath: str, value: Any) -> None:
    return None


def dt(obj: Any = None, trnsfrm: Any = None) -> Any:
    return obj


def get(obj: Any, field: str, defaultParam: Any = None) -> Any:
    """Walk a dotted path through nested dicts, like the XSOAR builtin."""
    cur = obj
    for part in field.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return defaultParam
    return cur


def gets(obj: Any, field: str) -> Any:
    return str(get(obj, field, ""))


def createIncidents(incidents: list[Any], lastRun: Any = None, userID: Any = None) -> list[Any]:
    return incidents


def addEntry(id: str, entry: Any, username: Any = None, email: Any = None, footer: Any = None) -> None:
    return None


def mirrorInvestigation(id: str, mirrorType: str, autoClose: bool = False) -> None:
    return None


def directMessage(message: str, username: Any = None, email: Any = None, anyoneCanOpenIncidents: Any = None) -> None:
    return None


def findUser(username: Any = None, email: Any = None) -> dict[str, Any]:
    return {}


def handleEntitlementForUser(incidentID: str, guid: str, email: str, content: str, taskID: str = "") -> None:
    return None


def mapObject(obj: Any, mapper: Any, mapper_type: Any) -> Any:
    return obj


def updateModuleHealth(data: Any, is_error: bool = False) -> None:
    return None


# Commands and server APIs: not available standalone


def _unsupported(name: str) -> list[dict[str, Any]]:
    return [{"Type": 4, "ContentsFormat": "text", "Contents": name + " is not supported outside XSOAR"}]


def executeCommand(command: str, args: Any = None) -> list[dict[str, Any]]:
    return _unsupported("executeCommand(" + str(command) + ")")


def executeCommandBatch(commands_list: Any) -> list[dict[str, Any]]:
    return _unsupported("executeCommandBatch")


def internalHttpRequest(method: str, uri: str, body: Any = None) -> dict[str, Any]:
    return {"statusCode": 501, "body": "internalHttpRequest is not supported outside XSOAR"}


def createIndicators(indicators_batch: Any, noUpdate: bool = False) -> None:
    return None


def searchIndicators(**kwargs: Any) -> dict[str, Any]:
    return {"iocs": [], "total": 0}


def searchRelationships(args: Any) -> dict[str, Any]:
    return {"data": [], "total": 0}


# State


def getLastRun() -> dict[str, Any]:
    return dict(_last_run)


def setLastRun(obj: Any) -> None:
    _last_run.clear()
    if isinstance(obj, dict):
        _last_run.update(obj)


def getLastMirrorRun() -> dict[str, Any]:
    return {}


def setLastMirrorRun(obj: Any) -> None:
    return None


def getAssetsLastRun() -> dict[str, Any]:
    return {}


def setAssetsLastRun(obj: Any) -> None:
    return None


def getIntegrationContext() -> dict[str, Any]:
    return dict(integrationContext)


def setIntegrationContext(context: Any) -> None:
    integrationContext.clear()
    if isinstance(context, dict):
        integrationContext.update(context)


def getIntegrationContextVersioned(refresh: bool = False) -> dict[str, Any]:
    return {"context": dict(integrationContext), "version": 0}


def setIntegrationContextVersioned(context: Any, version: int = -1, sync: bool = False) -> None:
    setIntegrationContext(context)


# Files


def uniqueFile() -> str:
    return str(uuid.uuid4())


def getFilePath(id: str) -> dict[str, str]:
    return {"id": id, "path": "/tmp/" + str(id), "name": str(id)}
