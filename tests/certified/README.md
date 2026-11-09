# Certified integrations

Contract tests for the threat-intel integrations dbot certifies: VirusTotal (API v3), AbuseIPDB,
AlienVault OTX v2, GreyNoise and urlscan.io.

```
uv run pytest tests/certified -v
```

Each test drives the real `Gateway` -> real `DockerSandbox` -> the integration's own demisto image. The
sandbox egress proxy (`DBOT_EGRESS_PROXY`) is a `mitmdump` process with `replay_addon.py`, bound to the
docker bridge gateway (172.17.0.1 on Linux and GitHub runners). It answers every request from
`fixtures/*.json` and never dials out (`connection_strategy=lazy`). An unmatched request gets HTTP 599
with the request line in the body, and the line is logged, so a missing fixture fails loudly. The
mitmproxy CA is mounted into the sandbox with `DockerSandbox(ca_bundle=...)`, so integrations keep TLS
verification on.

Per integration, `test_threat_intel.py` checks:

- `test-module` returns `status=ok`;
- each curated read command returns `ok`, non-empty `human_readable`, and at least one `outputs` context
  path declared in the YAML;
- a revoked key (the fixture answers 401) gives `status=error`, `error_kind=integration`, with the vendor
  message where the integration surfaces it;
- the policy shipped in `deploy/dbot.yaml.example` classifies the curated commands as `read` and the
  submit/report commands as `destructive`.

## Fixture format

One file per instance, `fixtures/<instance>.json`. Routes are tried in order; the first match wins.

```json
{
  "integration": "AbuseIPDB",
  "routes": [
    {
      "name": "ip check",
      "source": "content/Packs/AbuseDB/Integrations/AbuseDB/AbuseDB_test.py::test_check_ip_command expected_res",
      "method": "GET",
      "host": "api.abuseipdb.com",
      "path": "/api/v2/check",
      "query": {"ipAddress": "1.1.1.1"},
      "headers": {"Key": "dbot-replay-revoked-key"},
      "body_contains": "optional substring of the request body",
      "response": {"status": 200, "headers": {}, "body": {"data": {}}}
    }
  ]
}
```

`query` and `headers` are subsets that must match exactly (header names are case-insensitive). A string
`body` is sent as `text/plain`, anything else as JSON. Every route names its `source`: a file or constant in
the integration's own unit-test data, or "hand-written" with what it was based on. Error routes match on
the revoked key (`specs.BAD_KEY`), so they go first.

## Adding an integration

1. Add a `Spec` to `specs.py`: the exact `IntegrationDef.name`, the YAML path, the credentials param,
   curated read commands with args, the error command and expected message, and the submit/report
   commands that must be destructive.
2. Run `uv run pytest tests/certified -v -k <instance>`. Every request without a fixture fails with
   `dbot replay: no fixture for <METHOD> <URL>`.
3. Add a route for each such request to `fixtures/<instance>.json`. Take the body from
   `content/Packs/<pack>/Integrations/<name>/test_data/` or the integration's `*_test.py` where possible.
   Set the args in the spec to the values in that data (IPs, domains, hashes).
4. If a destructive command is classified `read`, propose a glob for the `policy:` block of
   `deploy/dbot.yaml.example`.

## Re-recording against a real tenant (manual, never in CI)

Fixtures must never contain real keys, account names or customer data. To refresh them:

1. Put real keys in your shell only, e.g. `export ABUSEIPDB_API_KEY=...`, and point a scratch
   `DBOT_CONFIG` at them with `env://` refs (see `deploy/dbot.yaml.example`). `Gateway.from_env()` has no
   CA bundle setting, so set `insecure: true` on the scratch instances; mitmdump still verifies the
   vendor's real certificate upstream. Never ship that setting.
2. Start a recording proxy: `uv run mitmdump --listen-host 172.17.0.1 --listen-port 8080 -w /tmp/flows`
   and export `DBOT_EGRESS_PROXY=http://172.17.0.1:8080`.
3. Run the commands, e.g. `uv run dbot invoke abuseipdb ip -a ip=1.1.1.1 -r "re-record fixtures"`.
4. Inspect with `uv run mitmproxy -r /tmp/flows`, copy the response bodies into the fixture routes, and
   scrub keys, account identifiers, emails and anything tenant-specific. Update `source` to say
   "recorded <date> from <vendor> API, scrubbed".
5. Delete `/tmp/flows`; it contains your key in request headers.
