"""Build function tools for the LlmAgent."""

from __future__ import annotations

from gemcode.config import GemCodeConfig
from gemcode.tools.bash import make_bash_tool
from gemcode.tools.edit import make_edit_tools
from gemcode.tools.filesystem import make_filesystem_tools
from gemcode.tools.notebook import make_notebook_tools
from gemcode.tools.repo_map import make_repo_map_tool
from gemcode.tools.search import make_grep_tool
from gemcode.tools.shell import make_run_command
from gemcode.tools.subtask import make_run_subtask_tool
from gemcode.tools.tasks import make_task_tools
from gemcode.tools.think import make_think_tool
from gemcode.tools.todo import make_todo_tool, make_todo_read_tool
from gemcode.tools.web import make_web_fetch_tool
from gemcode.tools.web_search import make_web_search_tool
from gemcode.checkpoints import list_checkpoints as _list_checkpoints, undo_checkpoint as _undo_checkpoint
from gemcode.tools.curated_memory import make_curated_memory_tools
from gemcode.tools.compress_memory import make_compress_memory_tool
from gemcode.tools.skills import make_skill_tools
from gemcode.tools.veomem_tools import make_veomem_tools
from gemcode.session_summariser import summarise_session


def _get_load_memory_tool():
  """Return ADK's built-in ``load_memory`` tool, or None if unavailable.

  ``load_memory`` lets the agent explicitly search its long-term memory store
  on demand (e.g. "what did I learn about this codebase?"), complementing
  ``preload_memory`` which only injects a fixed snapshot at turn start.
  """
  try:
    from google.adk.tools import load_memory
    return load_memory
  except Exception:
    return None


def _make_load_tool_result_tool(cfg: GemCodeConfig):
  def load_tool_result(ref: str, max_chars: int = 40_000, tail: bool = True) -> dict:
    """
    Load a previously offloaded tool output by reference.

    Offloaded outputs are created automatically when GEMCODE_TOOL_RESULT_OFFLOAD=1.
    References look like: tool_result:<sha256>.
    """
    from gemcode.tool_result_store import load_tool_result_text

    return load_tool_result_text(
      project_root=cfg.project_root,
      ref=ref,
      max_chars=max_chars,
      tail=tail,
    )

  return load_tool_result


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
  write_file, search_replace = make_edit_tools(cfg)
  todo_write = make_todo_tool(cfg)
  todo_read = make_todo_read_tool(cfg)
  think = make_think_tool()
  web_fetch = make_web_fetch_tool()
  web_search = make_web_search_tool()
  notebook_read, notebook_edit = make_notebook_tools(cfg)
  list_tasks, kill_task, task_output = make_task_tools(cfg)
  load_tool_result = _make_load_tool_result_tool(cfg)
  repo_map = make_repo_map_tool(cfg)
  remember_fact, read_curated_memory = make_curated_memory_tools(cfg)
  compress_memory_file = make_compress_memory_tool(cfg)
  list_skills, load_skill, skills_manifest = make_skill_tools(cfg)

  def summarise_session_tool(focus: str = "") -> dict:
    """
    Summarise the current session into compact reusable memory.

    Use this when the working session has grown large and you want GemCode to
    extract key points into durable notes + curated memory before continuing.
    """
    session_id = str(getattr(cfg, "_active_session_id", "") or "").strip()
    if not session_id:
      return {"error": "no active session id is available"}
    model = (
      getattr(cfg, "adk_compaction_summarizer_model", None)
      or getattr(cfg, "model", "")
      or "gemini-2.5-flash"
    )
    return summarise_session(
      cfg.project_root,
      session_id=session_id,
      model=model,
      focus=focus,
    )

  summarise_session_tool.__name__ = "summarise_session"

  def checkpoints_list(limit: int = 20) -> dict:
    """List recent checkpoints created by mutating tools."""
    return {"checkpoints": _list_checkpoints(cfg.project_root, limit=limit)}

  def checkpoint_undo(checkpoint_id: str | None = None) -> dict:
    """Undo the most recent checkpoint (or a specific checkpoint_id)."""
    return _undo_checkpoint(cfg.project_root, checkpoint_id=checkpoint_id)

  checkpoints_list.__name__ = "checkpoints_list"
  checkpoint_undo.__name__ = "checkpoint_undo"

  # Attach cfg for dynamic policy inside web_fetch (no cfg param in signature).
  try:
    setattr(web_fetch, "_cfg", cfg)
  except Exception:
    pass

  # bash and run_command are the most common long-running tools (builds, tests,
  # installs). Wrap them with LongRunningFunctionTool so ADK can handle slow
  # processes without hitting streaming timeouts.
  bash_tool = _wrap_long_running(bash)
  run_command_tool = _wrap_long_running(run_command)

  tools = [
    # Planning
    todo_write,
    todo_read,
    think,
    # File operations — read-only (batch these in parallel)
    read_file,
    list_directory,
    glob_files,
    grep_content,
    repo_map,
    # Notebooks
    notebook_read,
    notebook_edit,
    # Shell
    bash_tool,
    run_command_tool,
    # Background task management
    list_tasks,
    kill_task,
    task_output,
    # File mutations
    write_file,
    search_replace,
    move_file,
    delete_file,
    # Web / research
    web_search,
    web_fetch,
    # Tool output offload loader
    load_tool_result,
    # Self-healing: local checkpoints + undo
    checkpoints_list,
    checkpoint_undo,
    # Evolving: curated memory (safe-to-inject facts)
    remember_fact,
    read_curated_memory,
    # Optional: compress memory files (markdown only; safe guards apply)
    compress_memory_file,
    summarise_session_tool,
    # Optional: VeoMem recall tools (3-step search/timeline/fetch).
    # Enabled via GEMCODE_VEOMEM=1.
    # GemSkills (on-demand playbooks)
    list_skills,
    load_skill,
    skills_manifest,
  ]

  try:
    tools.extend(make_veomem_tools(cfg))
  except Exception:
    pass

  # ADK load_memory: explicit on-demand memory search (complements preload_memory).
  # Only add when memory is enabled so the tool doesn't appear when there's no
  # memory service to call into.
  if getattr(cfg, "enable_memory", False):
    lm = _get_load_memory_tool()
    if lm is not None:
      tools.append(lm)

  # run_subtask is excluded when building sub-agent tools (prevents recursion).
  if include_subtask:
    tools.append(make_run_subtask_tool(cfg))

  return tools
