from dbot.catalog.models import IntegrationDef, ParamDef
from dbot.sandbox.docker import default_params, map_output


def wire(results: list[object], exit: str = "ok", error: str | None = None) -> dict[str, object]:
    return {"results": results, "logs": ["INFO: x"], "exit": exit, "error": error}


def test_error_entry_wins_even_on_clean_exit() -> None:
    r = map_output("ip", wire([{"Type": 1, "Contents": "fine"}, {"Type": 4, "Contents": "404 Not Found"}]))
    assert (r.status, r.error_kind, r.error) == ("error", "integration", "404 Not Found")
    assert len(r.entries) == 2
    assert r.logs == ["INFO: x"]


def test_error_entry_wins_over_sysexit_and_exception() -> None:
    assert map_output("ip", wire([{"Type": 4, "Contents": "e"}], "sysexit")).error_kind == "integration"
    assert map_output("ip", wire([{"Type": 4, "Contents": "e"}], "exception", "X: y")).error_kind == "integration"


def test_exit_kinds() -> None:
    assert map_output("ip", wire([], "import", "ImportError: x")).error_kind == "import"
    r = map_output("ip", wire(["partial"], "exception", "KeyError: 'a'"))
    assert (r.status, r.error_kind, r.error) == ("error", "runtime", "KeyError: 'a'")
    assert r.entries[0].contents == "partial"
    assert map_output("ip", wire(["x"], "sysexit", "SystemExit: 2")).error_kind == "runtime"
    assert map_output("ip", wire([], "sysexit")).error_kind == "runtime"
    assert map_output("ip", wire(["x"], "sysexit")).status == "ok"
    assert map_output("ip", wire([], "garbage")).error_kind == "sandbox"


def test_test_module_needs_ok_entry() -> None:
    assert map_output("test-module", wire(["ok"])).status == "ok"
    r = map_output("test-module", wire(["Authorization failed"]))
    assert (r.status, r.error_kind, r.error) == ("error", "integration", "Authorization failed")
    assert map_output("test-module", wire([])).status == "error"
    # Only test-module is special-cased.
    assert map_output("ip", wire(["anything"])).status == "ok"


def test_context_merges_entry_contexts() -> None:
    r = map_output(
        "ip",
        wire(
            [
                {"Type": 1, "Contents": {}, "HumanReadable": "## a", "EntryContext": {"IP": {"Address": "1"}, "A": 1}},
                {"Type": 1, "Contents": {}, "EntryContext": {"IP": [{"Address": "2"}], "B": 2}},
                {"Type": 1, "Contents": {}, "EntryContext": None},
                "plain text",
            ]
        ),
    )
    assert r.context == {"IP": [{"Address": "1"}, {"Address": "2"}], "A": 1, "B": 2}
    assert r.entries[0].human_readable == "## a"
    assert r.entries[3].human_readable == "plain text"


def test_non_numeric_type_is_a_note() -> None:
    assert map_output("ip", wire([{"Type": "weird", "Contents": 1}])).status == "ok"


def test_default_params_types_booleans_and_skips_missing() -> None:
    integ = IntegrationDef(
        pack="p",
        name="n",
        py_path="/x.py",
        params=[
            ParamDef(name="url", default="https://api"),
            ParamDef(name="insecure", type=8, default="false"),
            ParamDef(name="proxy", type=8, default="True"),
            ParamDef(name="key"),
            ParamDef(name="unset_checkbox", type=8),
        ],
    )
    assert default_params(integ) == {"url": "https://api", "insecure": False, "proxy": True, "unset_checkbox": False}
