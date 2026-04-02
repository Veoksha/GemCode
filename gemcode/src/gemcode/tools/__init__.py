"""Build function tools for the LlmAgent."""

from __future__ import annotations

from gemcode.config import GemCodeConfig
from gemcode.tools.edit import make_edit_tools
from gemcode.tools.filesystem import make_filesystem_tools
from gemcode.tools.search import make_grep_tool
from gemcode.tools.shell import make_run_command


def build_function_tools(cfg: GemCodeConfig) -> list:
  read_file, list_directory, glob_files = make_filesystem_tools(cfg)
  grep_content = make_grep_tool(cfg)
  run_command = make_run_command(cfg)
  write_file, search_replace = make_edit_tools(cfg)
  return [
    read_file,
    list_directory,
    glob_files,
    grep_content,
    run_command,
    write_file,
    search_replace,
  ]
