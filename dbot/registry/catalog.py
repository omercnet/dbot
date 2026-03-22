"""In-memory searchable catalog of integration commands."""

import logging
from typing import Any

from dbot.registry.models import CommandDef, IntegrationDef

logger = logging.getLogger("dbot.catalog")


class Catalog:
    """In-memory searchable index of all integration commands."""

    def __init__(self, integrations: list[IntegrationDef]) -> None:
        self._integrations: dict[str, IntegrationDef] = {}
        self._commands: dict[str, tuple[IntegrationDef, CommandDef]] = {}

        for integration in integrations:
            self._integrations[integration.name] = integration
            for cmd in integration.commands:
                key = f"{integration.pack}.{cmd.name}"
                self._commands[key] = (integration, cmd)

        logger.info(
            "Catalog loaded: %d integrations, %d commands",
            len(self._integrations),
            len(self._commands),
        )

    def search(
        self,
        query: str,
        category: str | None = None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Keyword search over commands. Returns top_k matches."""
        query_terms = query.lower().split()
        results: list[tuple[int, str, IntegrationDef, CommandDef]] = []

        for key, (integration, cmd) in self._commands.items():
            if cmd.deprecated:
                continue
            if category:
                cat_lower = category.lower()
                # YAML categories are often inaccurate — also match against description
                cat_searchable = f"{integration.category} {integration.description}".lower()
                if cat_lower not in cat_searchable:
                    continue

            text = (
                f"{cmd.name} {cmd.description} {integration.pack} {integration.description} {integration.category}"
            ).lower()
            score = sum(1 for term in query_terms if term in text)

            if score > 0:
                results.append((score, key, integration, cmd))

        results.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "tool_name": key,
                "pack": integration.pack,
                "description": cmd.description,
                "category": integration.category,
                "args_summary": [{"name": a.name, "required": a.required} for a in cmd.args if not a.secret],
                "dangerous": cmd.dangerous,
            }
            for _score, key, integration, cmd in results[:top_k]
        ]

    def get_schema(self, tool_name: str) -> dict[str, Any]:
        """Get full schema for a specific tool. Secret args excluded."""
        if tool_name not in self._commands:
            raise KeyError(f"Tool '{tool_name}' not found. Use search_tools to discover available tools.")

        integration, cmd = self._commands[tool_name]
        return {
            "tool_name": tool_name,
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
                {
                    "context_path": o.context_path,
                    "description": o.description,
                    "type": o.type,
                }
                for o in cmd.outputs
            ],
        }

    def resolve(self, tool_name: str) -> tuple[IntegrationDef, CommandDef]:
        """Resolve a tool name to its integration and command definitions."""
        if tool_name not in self._commands:
            raise KeyError(f"Tool '{tool_name}' not found")
        return self._commands[tool_name]

    @property
    def stats(self) -> dict[str, Any]:
        """Return catalog statistics."""
        return {
            "total_integrations": len(self._integrations),
            "total_commands": len(self._commands),
            "categories": sorted({i.category for i in self._integrations.values() if i.category}),
        }
