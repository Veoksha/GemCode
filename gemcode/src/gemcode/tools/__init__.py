"""Build function tools for the LlmAgent."""

from __future__ import annotations

from gemcode.config import GemCodeConfig
from gemcode.tools.bash import make_bash_tool, make_bash_stream_tool
from gemcode.tools.edit import make_edit_tools
from gemcode.tools.filesystem import make_filesystem_tools
from gemcode.tools.search import make_grep_tool
from gemcode.tools.shell import make_run_command
from gemcode.tools.subtask import make_run_subtask_tool
from gemcode.tools.think import make_think_tool
from gemcode.tools.todo import make_todo_tool
from gemcode.tools.web import make_web_fetch_tool


def _wrap_long_running(fn):
  """
  Wrap a function tool with ADK's LongRunningFunctionTool so that long-running
  operations (npm install, cargo build, pytest, etc.) can run beyond the normal
  streaming timeout and yield intermediate updates.

  Falls back gracefully to the plain function if google-adk does not support
  LongRunningFunctionTool in the installed version.
  """
  try:
    from google.adk.tools import LongRunningFunctionTool
    return LongRunningFunctionTool(fn)
  except Exception:
    return fn


def build_function_tools(cfg: GemCodeConfig, *, include_subtask: bool = True) -> list:
  read_file, list_directory, glob_files, delete_file, move_file = make_filesystem_tools(cfg)
  grep_content = make_grep_tool(cfg)
  run_command = make_run_command(cfg)
  bash = make_bash_tool(cfg)
  bash_stream = make_bash_stream_tool(cfg)
  write_file, search_replace = make_edit_tools(cfg)
  todo_write = make_todo_tool(cfg)
  think = make_think_tool()
  web_fetch = make_web_fetch_tool()

  # bash and run_command are the most common long-running tools (builds, tests,
  # installs). Wrap them with LongRunningFunctionTool so ADK can handle slow
  # processes without hitting streaming timeouts.
  bash_tool = _wrap_long_running(bash)
  run_command_tool = _wrap_long_running(run_command)

  tools = [
    todo_write,
    think,
    read_file,
    list_directory,
    glob_files,
    grep_content,
    bash_tool,
    bash_stream,
    run_command_tool,
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
