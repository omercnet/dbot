# Integration Guide

How to add, validate, and use XSOAR integrations with dbot.

---

## How Integrations Work

Each integration in `demisto/content` is a Python file paired with a YAML
definition:

```
Packs/VirusTotal/Integrations/VirusTotalV3/
├── VirusTotalV3.py      # The actual integration code
├── VirusTotalV3.yml     # Command definitions, args, outputs
└── VirusTotalV3_test.py # Original XSOAR tests (not used by dbot)
```

The YAML defines what the integration can do. The Python does it.

---

## Adding a New Pack

### Step 1: Add to Sparse Checkout

```bash
cd content
git sparse-checkout add Packs/YourPackName
cd ..
```

### Step 2: Add to enabled_packs.yaml (Optional)

If you're using an enabled packs list:

```yaml
# config/enabled_packs.yaml
enabled_packs:
  - VirusTotal
  - YourPackName  # add here
```

If no `enabled_packs.yaml` exists, all packs in the submodule are indexed.

### Step 3: Add Credentials

```yaml
# config/credentials.yaml
YourPackName:
  apikey: ${YOUR_API_KEY}
```

### Step 4: Restart the Server

```bash
uv run python -m dbot.server
```

The new pack's commands will appear in `search_tools` results.

### Step 5: Test It

```
search_tools("your pack keyword")
  -> shows available commands

get_tool_schema("YourPackName.your-command")
  -> shows arguments and outputs

invoke_tool("YourPackName.your-command", {"arg": "value"}, "testing")
  -> executes and returns results
```

---

## Priority Integrations (Tier 1)

These integrations are included in the sparse checkout and prioritized for
validation:

### Threat Intelligence
| Pack | Key Commands |
|------|-------------|
| VirusTotal | `file`, `ip`, `domain`, `url` |
| Shodan | `search`, `host` |
| AbuseIPDB | `check-ip`, `check-block` |
| AlienVault OTX | `ip`, `domain`, `file` |
| MISP | `search-events`, `get-event` |

### Endpoint
| Pack | Key Commands |
|------|-------------|
| CrowdStrike Falcon | `get-incidents`, `get-detections`, `endpoint-isolation` (dangerous) |
| SentinelOne | `get-threats`, `get-agents`, `isolate-machine` (dangerous) |
| Wazuh | `get-alerts`, `get-agents` |

### SIEM / Logging
| Pack | Key Commands |
|------|-------------|
| Splunk | `search`, `submit-events` |
| Microsoft Sentinel | `get-incidents`, `get-alerts` |

### Case Management
| Pack | Key Commands |
|------|-------------|
| TheHive | `get-case`, `create-case`, `search-cases` |
| PagerDuty | `get-incidents`, `create-incident` |
| Jira | `get-issue`, `create-issue` |

### Cloud
| Pack | Key Commands |
|------|-------------|
| AWS GuardDuty | `get-findings` |
| AWS CloudTrail | `lookup-events` |
| Azure AD | `get-user`, `list-users` |

---

## Dangerous Commands

Commands marked `execution: true` in the XSOAR YAML are flagged dangerous.
These are typically destructive or high-impact actions:

- Host isolation (CrowdStrike, SentinelOne)
- Account suspension / session revocation
- Firewall rule changes
- IP/domain blocking

When `invoke_tool` is called on a dangerous command, it returns:

```json
{
  "status": "approval_required",
  "tool_name": "CrowdStrikeFalcon.endpoint-isolation",
  "args": {"device_id": "abc123"},
  "reason": "Active C2 beacon detected",
  "description": "endpoint-isolation is a dangerous operation. Human approval required."
}
```

The agent must surface this to a human operator for approval.

---

## Writing Validation Tests

Each integration should have a test that proves it loads and dispatches
correctly:

```python
# tests/integrations/test_yourpack.py
import pytest
from pathlib import Path
from dbot.runtime.common_server import bootstrap_common_modules
from dbot.runtime.executor import execute_inprocess

CONTENT_ROOT = Path(__file__).parent.parent.parent / "content"
INTEGRATION_PY = (
    CONTENT_ROOT / "Packs" / "YourPack" / "Integrations" / "YourIntegration"
    / "YourIntegration.py"
)

@pytest.fixture(scope="module", autouse=True)
def bootstrap():
    if not CONTENT_ROOT.exists():
        pytest.skip("content submodule not initialized")
    bootstrap_common_modules(CONTENT_ROOT)

class TestYourPack:
    @pytest.mark.asyncio
    async def test_loads(self):
        """Integration imports without crashing."""
        if not INTEGRATION_PY.exists():
            pytest.skip("Integration not found in sparse checkout")
        result = await execute_inprocess(
            INTEGRATION_PY,
            command="test-module",
            args={},
            params={"apikey": "fake", "insecure": True},
        )
        # May fail on HTTP (no real key), but should NOT crash on import
        assert result is not None

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.environ.get("YOUR_API_KEY"),
        reason="No API key"
    )
    async def test_live(self):
        """Live test with real API key."""
        result = await execute_inprocess(
            INTEGRATION_PY,
            command="your-command",
            args={"indicator": "8.8.8.8"},
            params={"apikey": os.environ["YOUR_API_KEY"]},
        )
        assert result["success"] is True
```

### Test Levels

| Level | What | When |
|-------|------|------|
| **Load test** | Module imports without error | Always (no API key needed) |
| **Dispatch test** | `command()` routes correctly | Always |
| **Mock test** | Mocked HTTP returns expected results | CI |
| **Live test** | Real API key, real data | CI with secrets |

---

## Common Issues

### Module-level `demisto.params()` Calls

Some integrations call `demisto.params()` at the top of the file, outside
`main()`. This means the mock must be set **before** the module is imported.
The subprocess executor handles this correctly -- the mock is set before
`exec_module()` runs.

### Missing Python Dependencies

Some integrations import third-party packages not included in dbot's
dependencies. Install them as needed:

```bash
uv pip install dateparser tldextract
```

### Non-Standard Integration Patterns

A few integrations don't follow the standard `main()` pattern. These may
need custom handling. Check the integration's Python file for its entry
point.

### `CommonServerPython` Import Errors

If you see errors about `distutils` or `DemistoClassApiModule`, the
integration may use deprecated Python features. The test fixtures in
`tests/test_common_server.py` show how to shim these.

---

Next: [Credentials](credentials.md) | [Architecture](architecture.md)
