"""
Think tool — structured reasoning scratchpad before acting.

Inspired by common agent "think" tool patterns.

Even though Gemini 2.5 has native dynamic thinking, an explicit think() call
improves quality on hard tasks because:
1. It forces a structured reasoning checkpoint before irreversible actions.
2. The written thought persists in the conversation history so later tool calls
   can reference it.
3. It creates a visible chain-of-reasoning the model can revise.
"""

from __future__ import annotations


def make_think_tool():
    def think(thought: str) -> dict:
        """
        Write out your reasoning, plan, or analysis before taking action.

        This is a private scratchpad — the content is NOT shown to the user.
        Use it when you need to think clearly before executing tools:

        - Planning a multi-step edit: map out what files to touch and in what order
        - Debugging an error: trace the execution path before deciding on a fix
        - Weighing approaches: compare trade-offs before committing to one
        - Verifying understanding: articulate your model of the code before editing
        - Decomposing a complex task: break it into concrete sub-steps
        - After reading multiple files: synthesise before writing

        Call think() BEFORE a sequence of tool calls, especially before destructive
        or irreversible actions (deletes, overwrites, force-pushes).

        The return value is intentionally empty — the value is in the reasoning
        itself being part of the conversation context.
        """
        return {"ok": True}

    return think
