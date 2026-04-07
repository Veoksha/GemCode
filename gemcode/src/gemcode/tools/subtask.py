"""
Sub-agent tool — spawn an isolated focused agent for context-heavy tasks.

Analogous to Reference UI AgentTool / AI SDK's ToolLoopAgent subagent pattern.

Key insight (from AI SDK docs "Subagents"):
  "Some tasks require exploring large amounts of information — reading files,
   searching codebases, or researching topics. Running these in the main agent
   consumes context quickly, making the agent less coherent over time.
   With subagents, you can spin up a dedicated agent that uses hundreds of
   thousands of tokens and have it return only a focused summary."

The sub-agent:
- Gets the same function tools as the parent (read_file, bash, grep, etc.) EXCEPT
  run_subtask itself (prevents infinite recursion).
- Also gets notes tools, ADK special tools, and modality extras when available.
- If memory is ON, the sub-agent gets `preload_memory` so it can access project history.
- Uses an in-memory session (no DB writes, fully isolated).
- Respects the same permission settings (yes_to_all, permission_mode) as the parent.
- Is depth-capped (max 64 LLM calls) to prevent runaway cost.
"""

from __future__ import annotations

from gemcode.config import GemCodeConfig


def _build_sub_tools(cfg: GemCodeConfig) -> list:
    """Build the full tool surface for a sub-agent (mirrors create_runner but in-process)."""
    from gemcode.tools import build_function_tools

    # Core function tools, minus run_subtask (no recursion).
    tools = build_function_tools(cfg, include_subtask=False)

    # Memory preload — if the parent has memory ON, sub-agents should too.
    if getattr(cfg, "enable_memory", False):
        try:
            from google.adk.tools import preload_memory
            tools = [preload_memory, *tools]
        except Exception:
            pass

    # ADK special interactive tools — always try to include.
    try:
        from google.adk.tools import get_user_choice, load_artifacts, exit_loop
        tools = [*tools, get_user_choice, load_artifacts, exit_loop]
    except Exception:
        pass

    # Notes tools — project knowledge persists to and from sub-agents.
    try:
        from gemcode.tools.notes import build_notes_tools
        notes_tools = build_notes_tools(cfg.project_root)
        tools = [*tools, *notes_tools]
    except Exception:
        pass

    # Modality extras (deep research, embeddings semantic search) when enabled.
    # This closes the gap where sub-agents previously couldn't do research/semantic search.
    try:
        from gemcode.modality_tools import build_extra_tools as build_modality_extra_tools
        modality = build_modality_extra_tools(cfg)
        if modality:
            tools = [*tools, *modality]
    except Exception:
        pass

    return tools


def make_run_subtask_tool(cfg: GemCodeConfig):
    async def run_subtask(task: str, context: str = "") -> dict:
        """
        Spawn an isolated sub-agent to complete a focused, self-contained task.

        The sub-agent starts with a fresh context window — it does NOT inherit this
        conversation's history. It has access to all the same tools (read_file, bash,
        grep_content, web_fetch, semantic search, project notes, etc.) and returns
        its final response as `result`.

        Use when:
        - Exploring a large codebase section that would bloat your context
          ("read all 40 test files and summarise what each group tests")
        - Running deep analysis in parallel — issue multiple run_subtask calls
          in the same turn for genuine parallel execution across subsystems
        - Delegating a focused research or investigation task while staying
          high-level yourself
        - **Verification passes** — after implementing a change, spawn a sub-agent
          with "You are a strict code reviewer. Check these files for correctness,
          bugs, and consistency. Report PASS or FAIL with details."
        - Any task that requires 10+ file reads or multiple bash commands but
          whose output can be summarised in a paragraph

        The sub-agent reasons and acts like a full Gemini coding agent.
        Give it a clear, self-contained task — include all context it needs
        since it cannot see this conversation.

        Tip: end your task description with "Summarise your findings clearly
        since your full output will be returned to the main agent."

        Args:
            task: Clear, self-contained description of what to do.
            context: Extra context the sub-agent needs (file paths, relevant
                     code snippets, constraints, acceptance criteria).
        """
        import uuid

        try:
            from google.adk.runners import Runner
            from google.adk.sessions.in_memory_session_service import (
                InMemorySessionService,
            )
        except ImportError:
            try:
                from google.adk.runners import Runner
                from google.adk.sessions import InMemorySessionService
            except ImportError:
                return {"error": "google-adk not available for sub-agent"}

        from gemcode.agent import build_root_agent
        from gemcode.invoke import run_turn
        from gemcode.plugins.tool_recovery_plugin import GemCodeReflectAndRetryToolPlugin

        # Build the full sub-agent tool surface.
        sub_tools = _build_sub_tools(cfg)

        # Build a standalone LlmAgent with the full tool set.
        sub_agent = build_root_agent(cfg, _tools=sub_tools)

        # Isolated in-memory session — never writes to the parent SQLite DB.
        # Include the reflect-and-retry plugin so sub-agents also benefit from
        # automatic tool error recovery.
        sub_plugins = [GemCodeReflectAndRetryToolPlugin(cfg)]
        try:
            from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin
            from gemcode.agent import build_global_instruction
            sub_plugins.insert(0, GlobalInstructionPlugin(build_global_instruction()))
        except Exception:
            pass

        try:
            from google.adk.apps.app import App
            sub_app = App(name="gemcode_sub", root_agent=sub_agent, plugins=sub_plugins)
            sub_runner = Runner(app=sub_app, session_service=InMemorySessionService())
        except Exception:
            # Legacy fallback for older ADK installs.
            sub_runner = Runner(
                app_name="gemcode_sub",
                agent=sub_agent,
                session_service=InMemorySessionService(),
                plugins=sub_plugins,
            )
        sub_session_id = str(uuid.uuid4())

        # Compose the sub-agent prompt.
        task_clean = task.strip()
        ctx_clean = (context or "").strip()
        prompt = task_clean
        if ctx_clean:
            prompt = f"{task_clean}\n\nAdditional context:\n{ctx_clean}"

        # Enforce a compact response contract to protect the parent context.
        prompt = (
            "Return a concise result using this exact structure:\n"
            "## Summary\n"
            "- <3-7 bullets>\n\n"
            "## Findings\n"
            "- <key technical findings>\n\n"
            "## Evidence (paths / commands)\n"
            "- <file paths, symbols, or commands you used>\n\n"
            "Do NOT include long code blocks or raw logs. If something is long, summarize it.\n\n"
            + prompt
        )

        # Sub-agents get a higher cap than before (64 vs 48) since they now
        # carry a richer tool surface (research, notes, etc.)
        sub_max_calls = min(int(cfg.max_llm_calls or 64), 64)

        try:
            events = await run_turn(
                sub_runner,
                user_id="local",
                session_id=sub_session_id,
                prompt=prompt,
                max_llm_calls=sub_max_calls,
                cfg=cfg,
            )
        except Exception as e:
            return {"error": f"Sub-agent error: {type(e).__name__}: {e}"}

        # Extract only non-thinking text parts from the sub-agent's output.
        parts: list[str] = []
        for ev in events:
            try:
                if not ev.content or not ev.content.parts:
                    continue
                if getattr(ev, "author", None) == "user":
                    continue
                for part in ev.content.parts:
                    t = getattr(part, "text", None)
                    is_thought = getattr(part, "thought", None)
                    if isinstance(t, str) and t.strip() and not is_thought:
                        parts.append(t)
            except Exception:
                continue

        result_text = "".join(parts).strip()
        if not result_text:
            result_text = (
                "(Sub-agent completed the task but produced no text output. "
                "It may have only called tools. Consider a more explicit task "
                "that asks the sub-agent to summarise its findings.)"
            )

        # Hard-cap sub-agent output; offload the full text if it exceeds the cap.
        max_chars = 8_000
        if len(result_text) > max_chars:
            try:
                from gemcode.tool_result_store import offload_text
                ref_obj = offload_text(
                    project_root=cfg.project_root,
                    tool_name="run_subtask",
                    field="result",
                    text=result_text,
                    preview_max_chars=max_chars,
                )
                return {
                    "result": ref_obj.get("preview", "") or "",
                    "offloaded": True,
                    "ref": ref_obj.get("ref"),
                    "note": "Subtask output was long; full text offloaded. Use load_tool_result(ref).",
                }
            except Exception:
                result_text = result_text[:max_chars] + "\n… [truncated]"
                return {"result": result_text, "truncated": True}

        return {"result": result_text}

    return run_subtask
