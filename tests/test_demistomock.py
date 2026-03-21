import pytest

from dbot.runtime.demistomock import DemistoMock, _get_mock, _reset_mock, _set_mock


class TestDemistoMock:
    def test_command_returns_injected_value(self) -> None:
        mock = DemistoMock(command="test-cmd", args={}, params={})
        assert mock.command() == "test-cmd"

    def test_args_returns_copy(self) -> None:
        original = {"key": "value"}
        mock = DemistoMock(command="", args=original, params={})
        result = mock.args()
        assert result == original
        result["key"] = "modified"
        assert mock.args()["key"] == "value"

    def test_params_returns_copy(self) -> None:
        original = {"apikey": "secret"}
        mock = DemistoMock(command="", args={}, params=original)
        result = mock.params()
        result["apikey"] = "changed"
        assert mock.params()["apikey"] == "secret"

    def test_results_captured_dict(self) -> None:
        mock = DemistoMock(command="", args={}, params={})
        mock.results({"Type": 1, "Contents": "hello"})
        assert len(mock.get_results()) == 1
        assert mock.get_results()[0]["Contents"] == "hello"

    def test_results_captured_list(self) -> None:
        mock = DemistoMock(command="", args={}, params={})
        mock.results([{"Contents": "a"}, {"Contents": "b"}])
        assert len(mock.get_results()) == 2

    def test_context_var_isolation(self) -> None:
        mock1 = DemistoMock(command="cmd1", args={}, params={})
        mock2 = DemistoMock(command="cmd2", args={}, params={})
        token1 = _set_mock(mock1)
        assert _get_mock().command() == "cmd1"
        token2 = _set_mock(mock2)
        assert _get_mock().command() == "cmd2"
        _reset_mock(token2)
        assert _get_mock().command() == "cmd1"
        _reset_mock(token1)

    def test_logs_captured(self) -> None:
        mock = DemistoMock(command="", args={}, params={})
        mock.info("info msg")
        mock.debug("debug msg")
        mock.error("error msg")
        logs = mock.get_logs()
        assert len(logs) == 3
        assert logs[0] == ("INFO", "info msg")
        assert logs[1] == ("DEBUG", "debug msg")
        assert logs[2] == ("ERROR", "error msg")

    def test_stubbed_methods_dont_raise(self) -> None:
        mock = DemistoMock(command="", args={}, params={})
        mock.setIntegrationContext({})
        mock.setLastRun({})
        mock.incidents()
        assert mock.getFilePath("x") == {"path": "/tmp/file", "name": "file"}
        assert mock.investigation() == {"id": "0"}
        assert mock.executeCommand("cmd", {}) == []
        assert mock.getIntegrationContext() == {}
        assert mock.context() == {}

    def test_get_and_gets(self) -> None:
        mock = DemistoMock(command="", args={}, params={})
        obj = {"a": 1, "b": 2}
        assert mock.get(obj, "a") == 1
        assert mock.get(obj, "c", "default") == "default"
        assert mock.gets(obj, "a") == 1
        with pytest.raises(KeyError):
            mock.gets(obj, "c")
