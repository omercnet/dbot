"""Squid egress allowlist: the union of every enabled instance's allowed hosts.

Usage: ``python -m dbot.egress PATH`` (or ``dbot egress write PATH``) writes deploy/allowlist.txt from DBOT_CONFIG.
"""

import re
import sys
from pathlib import Path

from dbot.control.config import DbotConfig, default_allow_hosts, load_config

# A hostname or IP, optionally with a leading "." (squid: also match subdomains). Anything else could inject
# extra lines or ACL syntax into the squid file.
_HOST_RE = re.compile(r"^\.?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")


def allowlist(config: DbotConfig) -> list[str]:
    """Sorted, de-duplicated hosts that enabled instances may reach. Raises ValueError on an invalid host."""
    hosts: set[str] = set()
    for inst in config.instances:
        if not inst.enabled:
            continue
        for raw in default_allow_hosts(inst):
            host = raw.strip().lower()
            if not _HOST_RE.match(host):
                raise ValueError(f"instance '{inst.name}': invalid allow_hosts entry {raw!r}")
            hosts.add(host)
    return sorted(hosts)


def write_squid_acl(path: str | Path, config: DbotConfig | None = None) -> list[str]:
    """Write the allowlist (one host per line, squid ``dstdomain`` file format) and return it."""
    hosts = allowlist(config if config is not None else load_config())
    text = "".join(f"{h}\n" for h in hosts)
    if str(path) in ("-", "/dev/stdout"):
        sys.stdout.write(text)
    else:
        Path(path).write_text(text, encoding="utf-8")
    return hosts


if __name__ == "__main__":
    write_squid_acl(sys.argv[1] if len(sys.argv) > 1 else "-")
