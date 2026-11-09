"""In-memory catalog of integration commands with weighted keyword search."""

import difflib
import logging
import math
import re
from collections import defaultdict
from typing import Any

from dbot.catalog.models import CommandDef, IntegrationDef

logger = logging.getLogger("dbot.catalog")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")

# Field weights: identity (integration name/display/pack) and command name beat descriptions.
_IDENT, _COMMAND, _CMD_DESC, _INT_DESC = range(4)
_WEIGHTS = (4.0, 3.0, 1.0, 0.5)
_PREFIX_FACTOR = 0.6
_MIN_PREFIX_LEN = 3


def tokenize(text: str) -> list[str]:
    """Case-fold and split on non-alphanumerics."""
    return _TOKEN_RE.findall(text.casefold())


def _name_tokens(text: str) -> set[str]:
    """Tokens of a name plus its camelCase pieces ("AbuseIPDB" -> abuseipdb, abuse, ipdb)."""
    pieces = {p.casefold() for word in re.split(r"[^A-Za-z0-9]+", text) for p in _CAMEL_RE.findall(word)}
    return set(tokenize(text)) | pieces


class Catalog:
    """Searchable index of integration commands. Tool names are `<IntegrationDef.name>.<command>`."""

    def __init__(self, integrations: list[IntegrationDef]) -> None:
        self._integrations: dict[str, IntegrationDef] = {}
        self._commands: dict[str, tuple[IntegrationDef, CommandDef]] = {}
        for integration in integrations:
            if integration.name in self._integrations:
                logger.warning("Duplicate integration name %r; keeping the last one", integration.name)
            self._integrations[integration.name] = integration
            for cmd in integration.commands:
                self._commands[f"{integration.name}.{cmd.name}"] = (integration, cmd)

        # Searchable docs: non-deprecated commands only.
        self._doc_keys: list[str] = []
        self._doc_name_tokens: list[set[str]] = []
        self._doc_cmd_tokens: list[set[str]] = []
        self._postings: list[dict[str, set[int]]] = [defaultdict(set) for _ in _WEIGHTS]
        self._df: dict[str, int] = defaultdict(int)
        for key, (integration, cmd) in self._commands.items():
            if cmd.deprecated:
                continue
            doc = len(self._doc_keys)
            self._doc_keys.append(key)
            name_tokens = set(tokenize(integration.name))
            cmd_tokens = set(tokenize(cmd.name))
            self._doc_name_tokens.append(name_tokens)
            self._doc_cmd_tokens.append(cmd_tokens)
            fields = (
                _name_tokens(f"{integration.name} {integration.display} {integration.pack}"),
                cmd_tokens,
                set(tokenize(cmd.description)),
                set(tokenize(integration.description)),
            )
            for postings, tokens in zip(self._postings, fields, strict=True):
                for token in tokens:
                    postings[token].add(doc)
            for token in set().union(*fields):
                self._df[token] += 1

        logger.info("Catalog loaded: %d integrations, %d commands", len(self._integrations), len(self._commands))

    def _match(self, term: str) -> dict[int, float]:
        """Best weighted match strength of one query term per doc (exact > prefix, ident > descriptions)."""
        best: dict[int, float] = {}
        for weight, postings in zip(_WEIGHTS, self._postings, strict=True):
            hits: list[tuple[set[int], float]] = []
            if term in postings:
                hits.append((postings[term], weight))
            if len(term) >= _MIN_PREFIX_LEN:
                prefix_score = weight * _PREFIX_FACTOR
                hits.extend(
                    (docs, prefix_score) for tok, docs in postings.items() if tok != term and tok.startswith(term)
                )
            for docs, score in hits:
                for doc in docs:
                    if score > best.get(doc, 0.0):
                        best[doc] = score
        return best

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Rank non-deprecated commands against a free-text query."""
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms or limit <= 0:
            return []
        n_docs = len(self._doc_keys)
        scores: dict[int, float] = defaultdict(float)
        for term in terms:
            idf = math.log(1.0 + n_docs / (1.0 + self._df.get(term, 0)))
            for doc, strength in self._match(term).items():
                scores[doc] += idf * strength

        query_set = set(terms)
        for doc in scores:
            # Tie-breakers: prefer integrations/commands whose names the query covers fully.
            name_tokens = self._doc_name_tokens[doc]
            cmd_tokens = self._doc_cmd_tokens[doc]
            if name_tokens:
                scores[doc] += len(name_tokens & query_set) / len(name_tokens)
            if cmd_tokens:
                scores[doc] += len(cmd_tokens & query_set) / len(cmd_tokens)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], self._doc_keys[item[0]]))
        results = []
        for doc, _score in ranked[:limit]:
            key = self._doc_keys[doc]
            integration, cmd = self._commands[key]
            results.append(
                {
                    "tool_name": key,
                    "integration": integration.name,
                    "display": integration.display,
                    "pack": integration.pack,
                    "description": cmd.description,
                    "dangerous": cmd.dangerous,
                }
            )
        return results

    def get(self, name: str) -> IntegrationDef:
        """Return an integration by its IntegrationDef.name."""
        try:
            return self._integrations[name]
        except KeyError:
            close = difflib.get_close_matches(name, self._integrations, n=3)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            raise KeyError(f"Integration '{name}' not found.{hint}") from None

    def resolve(self, tool_name: str) -> tuple[IntegrationDef, CommandDef]:
        """Resolve `<Integration.name>.<command>` to its integration and command definitions."""
        if tool_name in self._commands:
            return self._commands[tool_name]
        integration_name = next(
            (n for n in sorted(self._integrations, key=len, reverse=True) if tool_name.startswith(f"{n}.")), None
        )
        if integration_name is not None:
            cmds = ", ".join(c.name for c in self._integrations[integration_name].commands[:20])
            hint = f" Integration '{integration_name}' has commands: {cmds}."
        else:
            close = difflib.get_close_matches(tool_name, self._commands, n=3)
            hint = f" Did you mean: {', '.join(close)}?" if close else " Use search_tools to find tools."
        raise KeyError(f"Tool '{tool_name}' not found; tool names are '<Integration>.<command>'.{hint}")

    def get_schema(self, tool_name: str) -> dict[str, Any]:
        """Full schema for one tool. Secret args are excluded."""
        integration, cmd = self.resolve(tool_name)
        return {
            "tool_name": tool_name,
            "integration": integration.name,
            "display": integration.display,
            "pack": integration.pack,
            "description": cmd.description,
            "dangerous": cmd.dangerous,
            "arguments": [
                {
                    "name": a.name,
                    "description": a.description,
                    "required": a.required,
                    "default": a.default,
                    "is_array": a.is_array,
                    "options": a.options,
                }
                for a in cmd.args
                if not a.secret
            ],
            "outputs": [
                {"context_path": o.context_path, "description": o.description, "type": o.type} for o in cmd.outputs
            ],
        }

    @property
    def stats(self) -> dict[str, Any]:
        """Catalog counts."""
        integrations = self._integrations.values()
        return {
            "total_integrations": len(self._integrations),
            "total_commands": len(self._commands),
            "with_dockerimage": sum(1 for i in integrations if i.dockerimage),
            "with_apimodules": sum(1 for i in integrations if i.apimodules),
            "categories": sorted({i.category for i in integrations if i.category}),
        }
