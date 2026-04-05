"""
Iterative refinement pipelines using ADK LoopAgent + SequentialAgent.

This module exposes factory functions for building multi-agent orchestration
patterns that GemCode can invoke for complex, multi-step tasks:

  - build_refine_loop(): LoopAgent that loops write→test→fix until tests pass
    or max_iterations is reached (escalates via exit_loop tool).

  - build_sequential_pipeline(): SequentialAgent that runs N specialist agents
    in order, passing results through session state via output_key.

  - build_parallel_research(): ParallelAgent that fans out N research sub-agents
    then collects their outputs.

These are "heavy" patterns; they are NOT wired by default — callers instantiate
them on demand (e.g. via /refine slash command or run_subtask).
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guard: check what's available in the installed ADK version
# ---------------------------------------------------------------------------
try:
    from google.adk.agents import LoopAgent, SequentialAgent, ParallelAgent, LlmAgent  # type: ignore
    _ADK_WORKFLOW_AGENTS_OK = True
except ImportError:
    _ADK_WORKFLOW_AGENTS_OK = False
    log.warning("google.adk workflow agents (LoopAgent/SequentialAgent/ParallelAgent) "
                "not available — refine pipelines disabled.")


def _require_workflow() -> None:
    if not _ADK_WORKFLOW_AGENTS_OK:
        raise RuntimeError(
            "ADK workflow agents (LoopAgent / SequentialAgent / ParallelAgent) are not "
            "available in the installed google-adk version. "
            "Try: pip install -U google-adk"
        )


# ---------------------------------------------------------------------------
# Write → Test → Fix Loop
# ---------------------------------------------------------------------------

def build_refine_loop(
    *,
    model: str,
    task_description: str,
    test_command: str,
    max_iterations: int = 8,
    extra_tools: list | None = None,
) -> Any:
    """
    Build a LoopAgent that iteratively writes code, runs tests, and fixes
    failures until all tests pass or ``max_iterations`` is exhausted.

    Architecture:
      LoopAgent(max_iterations=N)
        ├─ writer_agent   — writes / edits code based on current task + test output
        ├─ tester_agent   — runs the test command via bash, stores result in state
        └─ checker_agent  — inspects test result; calls exit_loop if tests pass

    State keys used:
      "task"          — original task description (seeded by caller)
      "test_command"  — shell command to run tests
      "test_output"   — stdout/stderr from last test run
      "test_passed"   — "yes" if tests passed
      "iteration"     — current loop count (auto-incremented by writer)

    Returns the LoopAgent instance, ready to run via a Runner.

    Example usage:
      loop = build_refine_loop(model="gemini-2.5-flash",
                               task_description="add unit tests for parser.py",
                               test_command="pytest tests/test_parser.py -x")
      runner = Runner(agent=loop, app_name="refine", session_service=...)
      async for event in runner.run_async(...):
          ...
    """
    _require_workflow()
    from google.adk.tools import exit_loop  # type: ignore

    from gemcode.tools import build_function_tools  # type: ignore

    # Shared tools that the sub-agents may use (no run_subtask to avoid recursion)
    tools = extra_tools or []

    writer = LlmAgent(
        model=model,
        name="writer_agent",
        description="Writes or edits source code to fulfil the task, using test failure output as feedback.",
        instruction=(
            "You are a coding agent. Your goal: {task}.\n\n"
            "Test command: {test_command}\n"
            "Last test output:\n{test_output?}\n\n"
            "Write or fix code to make the tests pass. "
            "After making your edits, update state key 'iteration' by incrementing it."
        ),
        tools=tools,
        output_key="writer_notes",
    )

    tester = LlmAgent(
        model=model,
        name="tester_agent",
        description="Runs the test suite and stores the output in session state.",
        instruction=(
            "Run the test command stored in state: {test_command}.\n"
            "Use the bash tool to execute it. Store ALL stdout+stderr verbatim in "
            "state key 'test_output'. "
            "If the command exits with code 0 (success), set state key 'test_passed' to 'yes'. "
            "If it fails, set 'test_passed' to 'no'."
        ),
        tools=tools,
        output_key="test_output",
    )

    checker = LlmAgent(
        model=model,
        name="checker_agent",
        description=(
            "Checks whether tests passed. If yes, calls exit_loop to terminate the "
            "refinement cycle. If no, does nothing (loop continues)."
        ),
        instruction=(
            "Check the value of state key 'test_passed'.\n"
            "If 'test_passed' is 'yes': call the exit_loop tool to stop the loop — "
            "the task is complete.\n"
            "If 'test_passed' is 'no': output a brief summary of the failures so the "
            "writer knows what to fix next. Do NOT call exit_loop."
        ),
        tools=[exit_loop, *tools],
    )

    return LoopAgent(
        name="refine_loop",
        description=(
            f"Iterative write→test→fix loop for: {task_description[:120]}"
        ),
        sub_agents=[writer, tester, checker],
        max_iterations=max_iterations,
    )


# ---------------------------------------------------------------------------
# Sequential Pipeline
# ---------------------------------------------------------------------------

def build_sequential_pipeline(
    agents: list[Any],
    *,
    name: str = "sequential_pipeline",
    description: str = "Runs specialist agents sequentially, passing results via state.",
) -> Any:
    """
    Wrap a list of LlmAgents in a SequentialAgent.

    Each agent should set ``output_key`` so subsequent agents can read its output
    via {key} template substitution in their instructions.

    Example:
      pipeline = build_sequential_pipeline([
          planner_agent,   # output_key="plan"
          coder_agent,     # reads {plan}, output_key="code"
          reviewer_agent,  # reads {code}, output_key="review"
      ])
    """
    _require_workflow()
    return SequentialAgent(name=name, description=description, sub_agents=agents)


# ---------------------------------------------------------------------------
# Parallel Fan-Out
# ---------------------------------------------------------------------------

def build_parallel_research(
    agents: list[Any],
    *,
    name: str = "parallel_research",
    description: str = "Runs research agents in parallel, collecting results via state.",
) -> Any:
    """
    Wrap a list of LlmAgents in a ParallelAgent.

    Each agent MUST use a distinct ``output_key`` to avoid state key collisions.

    Example:
      research = build_parallel_research([
          web_researcher,   # output_key="web_results"
          code_searcher,    # output_key="code_results"
          doc_reader,       # output_key="doc_results"
      ])
      # Wrap in a SequentialAgent to collect after:
      pipeline = build_sequential_pipeline([research, synthesis_agent])
    """
    _require_workflow()
    return ParallelAgent(name=name, description=description, sub_agents=agents)
