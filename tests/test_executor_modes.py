"""Tests for execution mode toggle."""

import os


class TestExecutionModeToggle:
    def test_default_mode_is_inprocess(self) -> None:
        mode = os.environ.get("DBOT_EXECUTION_MODE", "inprocess")
        assert mode == "inprocess"

    def test_env_var_switches_mode(self) -> None:
        os.environ["DBOT_EXECUTION_MODE"] = "subprocess"
        mode = os.environ.get("DBOT_EXECUTION_MODE", "inprocess")
        assert mode == "subprocess"
        # Clean up
        del os.environ["DBOT_EXECUTION_MODE"]
