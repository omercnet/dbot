import json
from pathlib import Path

from dbot.audit import AuditLogger


class TestAuditLogger:
    def test_log_creates_jsonl_entry(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.log"
        audit = AuditLogger(audit_path=log_path)
        audit.log_invocation(
            tool_name="VirusTotal.vt-get-file",
            args={"file": "abc123"},
            reason="checking hash",
            dangerous=False,
            result={"success": True},
            duration_ms=123.45,
        )
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool_name"] == "VirusTotal.vt-get-file"
        assert entry["reason"] == "checking hash"
        assert entry["dangerous"] is False
        assert entry["result_success"] is True
        assert entry["duration_ms"] == 123.45
        assert "timestamp" in entry

    def test_multiple_entries_appended(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.log"
        audit = AuditLogger(audit_path=log_path)
        for i in range(3):
            audit.log_invocation(
                tool_name=f"tool-{i}",
                args={},
                reason="test",
                dangerous=False,
                result={"success": True},
                duration_ms=float(i),
            )
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_no_credentials_in_audit(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.log"
        audit = AuditLogger(audit_path=log_path)
        audit.log_invocation(
            tool_name="test",
            args={"ip": "1.2.3.4"},
            reason="check",
            dangerous=False,
            result={"success": True, "results": [{"password": "secret"}]},
            duration_ms=100.0,
        )
        content = log_path.read_text()
        assert "secret" not in content
        assert "password" not in content
