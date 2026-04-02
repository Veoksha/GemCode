"""Claude Code–style slash command parsing and built-ins for GemCode CLI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedSlashCommand:
  command_name: str
  args: str
  is_mcp: bool


def parse_slash_command(raw: str) -> ParsedSlashCommand | None:
  s = (raw or "").strip()
  if not s.startswith("/"):
    return None
  without = s[1:]
  if not without:
    return None
  words = without.split(" ")
  if not words[0]:
    return None
  command = words[0]
  is_mcp = False
  args_start = 1
  if len(words) > 1 and words[1] == "(MCP)":
    command = command + " (MCP)"
    is_mcp = True
    args_start = 2
  args = " ".join(words[args_start:]).strip()
  return ParsedSlashCommand(command_name=command, args=args, is_mcp=is_mcp)

