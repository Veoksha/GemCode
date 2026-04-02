from __future__ import annotations

from gemcode.slash_commands import parse_slash_command


def test_parse_slash_command_basic() -> None:
  p = parse_slash_command("/search foo bar")
  assert p is not None
  assert p.command_name == "search"
  assert p.args == "foo bar"
  assert p.is_mcp is False


def test_parse_slash_command_mcp() -> None:
  p = parse_slash_command("/mcp:tool (MCP) arg1 arg2")
  assert p is not None
  assert p.command_name == "mcp:tool (MCP)"
  assert p.args == "arg1 arg2"
  assert p.is_mcp is True


def test_parse_slash_command_invalid() -> None:
  assert parse_slash_command("hi") is None
  assert parse_slash_command("/") is None

