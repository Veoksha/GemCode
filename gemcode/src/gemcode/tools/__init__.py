"""Build function tools for the LlmAgent."""

from __future__ import annotations

from gemcode.config import GemCodeConfig
from gemcode.tools.bash import make_bash_tool
from gemcode.tools.edit import make_edit_tools
from gemcode.tools.filesystem import make_filesystem_tools
from gemcode.tools.search import make_grep_tool
from gemcode.tools.shell import make_run_command
from gemcode.tools.subtask import make_run_subtask_tool
from gemcode.tools.think import make_think_tool
from gemcode.tools.todo import make_todo_tool
from gemcode.tools.web import make_web_fetch_tool


def build_function_tools(cfg: GemCodeConfig, *, include_subtask: bool = True) -> list:
  read_file, list_directory, glob_files, delete_file, move_file = make_filesystem_tools(cfg)
  grep_content = make_grep_tool(cfg)
  run_command = make_run_command(cfg)
  bash = make_bash_tool(cfg)
  write_file, search_replace = make_edit_tools(cfg)
  todo_write = make_todo_tool(cfg)
  think = make_think_tool()
  web_fetch = make_web_fetch_tool()

  tools = [
    todo_write,
    think,
    read_file,
    list_directory,
    glob_files,
    grep_content,
    bash,
    run_command,
    write_file,
    search_replace,
    move_file,
    delete_file,
    web_fetch,
  ]

  # run_subtask is excluded when building sub-agent tools (prevents recursion).
  if include_subtask:
    tools.append(make_run_subtask_tool(cfg))

  return tools
