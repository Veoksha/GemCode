"""
Gemini Interactions API (optional durable / server-side history).

The ADK `LlmRequest` includes `previous_interaction_id` for chaining interactions.
Wiring GemCode to Interactions API can reduce client-side history size for long
sessions. Implementation is deferred: enable when your `google-adk` version and
deployment target require it, following:
https://ai.google.dev/gemini-api/docs/interactions

Planned integration points:
- Store `previous_interaction_id` in `.gemcode/session_meta.json` per session.
- Pass through Runner/App configuration when ADK exposes a stable hook for your stack.
"""

from __future__ import annotations
