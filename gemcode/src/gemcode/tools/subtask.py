"""
Sub-agent tool — spawn an isolated focused agent for context-heavy tasks.

Analogous to OpenClaude's AgentTool / AI SDK's ToolLoopAgent subagent pattern.

Key insight (from AI SDK docs "Subagents"):
  "Some tasks require exploring large amounts of information — reading files,
   searching codebases, or researching topics. Running these in the main agent
   consumes context quickly, making the agent less coherent over time.
   With subagents, you can spin up a dedicated agent that uses hundreds of
   thousands of tokens and have it return only a focused summary."

The sub-agent:
- Gets the same tools as the parent (read_file, bash, grep, etc.) EXCEPT
  run_subtask itself (prevents infinite recursion).
- Uses an in-memory session (no DB writes, fully isolated).
- Respects the same permission settings (yes_to_all, permission_mode) as the parent.
- Is depth-capped (max 48 LLM calls) to prevent runaway cost.
"""

from __future__ import annotations

from gemcode.config import GemCodeConfig


def make_run_subtask_tool(cfg: GemCodeConfig):
    async def run_subtask(task: str, context: str = "") -> dict:
        """
        Spawn an isolated sub-agent to complete a focused, self-contained task.

        The sub-agent starts with a fresh context window — it does NOT inherit this
        conversation's history. It has access to all the same tools (read_file, bash,
        grep_content, web_fetch, etc.) and returns its final response as `result`.

        Use when:
        - Exploring a large codebase section that would bloat your context
          ("read all 40 test files and summarise what each group tests")
        - Running deep analysis in parallel — issue multiple run_subtask calls
          in the same turn for genuine parallel execution
        - Delegating a focused research or investigation task while staying
          high-level yourself
        - Getting a verification or review pass on your own changes
          ("check my edits in src/ for syntax errors and consistency")
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
        from gemcode.tools import build_function_tools
        from gemcode.invoke import run_turn

        # Build the sub-agent tool set WITHOUT run_subtask (no recursion).
        sub_tools = build_function_tools(cfg, include_subtask=False)

        # Build a standalone LlmAgent with the limited tool set.
        sub_agent = build_root_agent(cfg, _tools=sub_tools)

        # Isolated in-memory session — never writes to the parent SQLite DB.
        sub_runner = Runner(
            app_name="gemcode_sub",
            agent=sub_agent,
            session_service=InMemorySessionService(),
        )
        sub_session_id = str(uuid.uuid4())

        # Compose the sub-agent prompt.
        prompt = task.strip()
        if context and context.strip():
            prompt = f"{task.strip()}\n\nAdditional context:\n{context.strip()}"

        # Cap sub-agent depth to avoid runaway API cost.
        sub_max_calls = min(int(cfg.max_llm_calls or 48), 48)

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

        return {"result": result_text}

    return run_subtask
