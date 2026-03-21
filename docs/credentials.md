# Credential Configuration

How to configure API keys and secrets for dbot integrations.

---

## Overview

Each integration needs credentials (API keys, client IDs, base URLs). These
are stored in `config/credentials.yaml` and injected automatically when a
tool is invoked. Credentials are **never exposed to the agent**.

---

## Setup

```bash
cp config/credentials.yaml.example config/credentials.yaml
```

Edit `config/credentials.yaml` with your actual values.

---

## Configuration Format

```yaml
PackName:
  param_name: value_or_env_reference
```

Values can be:
- **Literal strings**: `apikey: abc123def`
- **Environment variable references**: `apikey: ${VT_API_KEY}`

Environment variables are resolved at startup. If a referenced variable
is not set, dbot logs a warning and skips that pack.

---

## Per-Integration Examples

### VirusTotal

```yaml
VirusTotal:
  apikey: ${VT_API_KEY}
```

```bash
export VT_API_KEY=your_virustotal_api_key
```

### CrowdStrike Falcon

```yaml
CrowdStrikeFalcon:
  client_id: ${CS_CLIENT_ID}
  client_secret: ${CS_CLIENT_SECRET}
  base_url: https://api.crowdstrike.com
```

### Shodan

```yaml
Shodan:
  apikey: ${SHODAN_API_KEY}
```

### AbuseIPDB

```yaml
AbuseIPDB:
  apikey: ${ABUSEIPDB_API_KEY}
```

### Splunk

```yaml
Splunk:
  url: ${SPLUNK_URL}
  username: ${SPLUNK_USER}
  password: ${SPLUNK_PASS}
```

### MISP

```yaml
MISP:
  url: ${MISP_URL}
  apikey: ${MISP_API_KEY}
```

### TheHive

```yaml
TheHive:
  url: ${THEHIVE_URL}
  apikey: ${THEHIVE_API_KEY}
```

---

## How Credentials Flow

```
credentials.yaml
    |
    v
CredentialStore loads + resolves ${ENV_VAR} at startup
    |
    v
Agent calls invoke_tool("VirusTotal.vt-get-file", {"file": "abc123"}, "reason")
    |
    v
credential_store.get("VirusTotal") -> {"apikey": "actual-key"}
    |
    v
Merged into params dict, passed to executor
    |
    v
Integration receives params via demistomock.params()
    -> params["apikey"] = "actual-key"
```

The agent **never** sees the API key. It only passes non-secret arguments
like file hashes, IPs, and domains.

---

## Security Model

| Principle | Implementation |
|-----------|---------------|
| Agent never sees secrets | Secret args (YAML `type: 9`) stripped from tool schemas |
| Credentials resolved server-side | CredentialStore injects before execution |
| Env var preferred over hardcoded | `${VAR}` syntax encourages env-based secrets |
| Audit log excludes credentials | Only non-secret args and reason are logged |
| Process isolation | Subprocess executor limits credential exposure scope |

---

## Checking Which Packs Need Credentials

To see which credential params an integration requires, check its YAML:

```bash
grep -A 3 'type: 9' content/Packs/VirusTotal/Integrations/VirusTotalV3/VirusTotalV3.yml
```

Parameters with `type: 9` are credentials.

---

## Vault Integration (Future)

The `CredentialStore` interface is designed to be pluggable. A future
release will support:

- **HashiCorp Vault**: `vault://secret/data/dbot/virustotal#apikey`
- **AWS Secrets Manager**: `aws-sm://dbot/virustotal`
- **Azure Key Vault**: `az-kv://dbot-vault/virustotal-key`

The store interface (`get(pack_name) -> dict[str, str]`) stays the same.

---

Next: [Integration Guide](integrations.md) | [Architecture](architecture.md)
