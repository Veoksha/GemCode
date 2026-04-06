"""
LLM-based intent pre-classifier.

Before each user turn a lightweight Gemini call (gemini-2.5-flash-lite by
default) classifies the message into one of five intents.  The TUI / CLI
can then:

  - Short-circuit GREETING turns entirely (instant reply, zero tool calls)
  - Inject the intent label into session state so the main agent adapts its
    workflow automatically without re-classifying

Configure:
  GEMCODE_INTENT_MODEL  Override the classifier model (default: gemini-2.5-flash-lite)
  GEMCODE_INTENT_CLASSIFY_ENABLED  Set "0" to disable (falls back to main agent)
"""
from __future__ import annotations

import os

# ── Intent labels ─────────────────────────────────────────────────────────────
INTENT_GREETING          = "GREETING"
INTENT_CONCEPT           = "CONCEPT"
INTENT_PROJECT_QUESTION  = "PROJECT_QUESTION"
INTENT_ENGINEERING_TASK  = "ENGINEERING_TASK"
INTENT_ANALYSIS          = "ANALYSIS"

_VALID_INTENTS = {
    INTENT_GREETING,
    INTENT_CONCEPT,
    INTENT_PROJECT_QUESTION,
    INTENT_ENGINEERING_TASK,
    INTENT_ANALYSIS,
}

# Mapping from intent to a short description injected into the main agent's context
INTENT_DESCRIPTIONS: dict[str, str] = {
    INTENT_GREETING:         "Conversational greeting or social message — reply warmly, no tools.",
    INTENT_CONCEPT:          "General knowledge question — answer from knowledge, no project files needed.",
    INTENT_PROJECT_QUESTION: "Question about this specific codebase — use 1-2 read-only tools, then reply.",
    INTENT_ENGINEERING_TASK: "Code task (fix / add / refactor / debug) — orient → plan → execute → verify.",
    INTENT_ANALYSIS:         "Systematic audit or summarisation — thorough tool sweep, then synthesise.",
}

# One-line summaries for the TUI — same visual lane as ∴ Thinking (collapsed)
INTENT_THINKING_SUMMARY: dict[str, str] = {
    INTENT_GREETING:         "Greeting / chitchat — no tools",
    INTENT_CONCEPT:          "General knowledge — answer without repo reads if possible",
    INTENT_PROJECT_QUESTION: "About this repo — a few read-only tools, then answer",
    INTENT_ENGINEERING_TASK: "Engineering task — orient → plan → execute → verify",
    INTENT_ANALYSIS:         "Deep analysis — systematic read / grep / synthesise",
}

# How the intent was determined (for TUI suffix)
SOURCE_LOCAL = "local"   # obvious greeting / heuristic, no classifier API call
SOURCE_LLM = "llm"       # gemini-2.5-flash-lite classifier
SOURCE_OFF = "off"       # classifier disabled


def format_intent_thinking_line(intent: str, source: str) -> str | None:
    """
    Single line of text after ``∴ Intent`` in the TUI (same visual lane as
    collapsed ``∴ Thinking``).  Returns None when the classifier is off and
    nothing should be shown.
    """
    if source == SOURCE_OFF:
        return None
    summary = INTENT_THINKING_SUMMARY.get(intent, intent)
    if source == SOURCE_LOCAL:
        tag = "instant"
    else:
        tag = "flash-lite classifier"
    return f"{intent} — {summary}  ·  {tag}"

# ── Prompts ───────────────────────────────────────────────────────────────────
_CLASSIFY_PROMPT = """\
Classify the user message into exactly ONE intent label from the list below.

Labels:
  GREETING          — greetings, thanks, social messages, chitchat
                      (hi, hii, hello, hey, thanks, cool, nice, ok, goodbye, how are you, etc.)
  CONCEPT           — general knowledge question needing no project files
                      (what is X, explain Y, compare A vs B, best practice for Z)
  PROJECT_QUESTION  — question about THIS specific codebase
                      (how does auth work here, what does this file do, where is X defined)
  ENGINEERING_TASK  — request to write, fix, modify, debug, or implement code
  ANALYSIS          — request to systematically audit, review, summarise, or map the codebase

User message: "{message}"

Reply with ONLY the label name, nothing else."""

_GREETING_SYSTEM = (
    "You are GemCode, an expert coding assistant. "
    "The user sent you a short conversational message. "
    "Reply naturally and warmly in ONE brief sentence. "
    "Do not mention code, tools, files, or ask what they need."
)

_CLASSIFIER_MODEL_ENV = "GEMCODE_INTENT_MODEL"
_DEFAULT_CLASSIFIER_MODEL = "gemini-2.5-flash-lite"

# Single-word / very-short messages that are unambiguously greetings —
# checked locally before spending an API call on the classifier.
_OBVIOUS_GREETINGS: frozenset[str] = frozenset({
    "hi", "hii", "hiii", "hey", "hello", "heya", "hiya", "howdy", "sup", "yo",
    "thanks", "thank you", "thx", "ty", "thankyou",
    "cool", "nice", "great", "awesome", "ok", "okay", "k",
    "bye", "goodbye", "cya", "later",
    "good morning", "good evening", "good night", "good afternoon",
    "what's up", "whats up", "wassup",
})


def _classifier_enabled() -> bool:
    v = os.environ.get("GEMCODE_INTENT_CLASSIFY_ENABLED", "1")
    return v.lower() not in ("0", "false", "no", "off")


def _get_classifier_model() -> str:
    return os.environ.get(_CLASSIFIER_MODEL_ENV) or _DEFAULT_CLASSIFIER_MODEL


def _get_api_key() -> str:
    return (
        os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or ""
    )


async def classify_intent_with_source(message: str) -> tuple[str, str]:
    """
    Classify a user message; return (intent, source).

    ``source`` is one of: ``SOURCE_LOCAL`` (heuristic, no API call),
    ``SOURCE_LLM`` (classifier model), ``SOURCE_OFF`` (classifier disabled).
    """
    if not _classifier_enabled():
        return INTENT_ENGINEERING_TASK, SOURCE_OFF

    stripped = (message or "").strip()
    if not stripped:
        return INTENT_GREETING, SOURCE_LOCAL

    # Fast local check for unambiguously short greetings — saves an API round-trip.
    lower = stripped.lower()
    if lower in _OBVIOUS_GREETINGS or (len(lower) <= 3 and lower.isalpha()):
        return INTENT_GREETING, SOURCE_LOCAL

    try:
        import google.genai as genai
        from google.genai import types as gtypes

        client = genai.Client(api_key=_get_api_key())
        resp = await client.aio.models.generate_content(
            model=_get_classifier_model(),
            contents=_CLASSIFY_PROMPT.format(message=stripped[:600]),
            config=gtypes.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=15,
            ),
        )
        label = (resp.text or "").strip().upper()
        # Exact match first
        if label in _VALID_INTENTS:
            return label, SOURCE_LLM
        # Partial match (model may return extra punctuation or lowercase)
        for key in _VALID_INTENTS:
            if key in label:
                return key, SOURCE_LLM
        return INTENT_ENGINEERING_TASK, SOURCE_LLM
    except Exception:
        return INTENT_ENGINEERING_TASK, SOURCE_LLM


async def classify_intent(message: str) -> str:
    """
    Classify a user message using a lightweight Gemini call.

    Returns one of: GREETING, CONCEPT, PROJECT_QUESTION, ENGINEERING_TASK, ANALYSIS.
    Falls back to ENGINEERING_TASK on any error (the main agent handles all real
    tasks safely with that default).

    Classification is disabled when GEMCODE_INTENT_CLASSIFY_ENABLED=0.
    """
    intent, _ = await classify_intent_with_source(message)
    return intent


async def generate_greeting_reply(message: str) -> str:
    """
    Generate a warm, natural one-sentence reply for a greeting message.

    Uses the same lightweight classifier model so the response is instant.
    Falls back to a generic reply on any error.
    """
    try:
        import google.genai as genai
        from google.genai import types as gtypes

        client = genai.Client(api_key=_get_api_key())
        resp = await client.aio.models.generate_content(
            model=_get_classifier_model(),
            contents=[
                gtypes.Content(
                    role="user",
                    parts=[gtypes.Part(text=_GREETING_SYSTEM)],
                ),
                gtypes.Content(
                    role="model",
                    parts=[gtypes.Part(text="Got it.")],
                ),
                gtypes.Content(
                    role="user",
                    parts=[gtypes.Part(text=message)],
                ),
            ],
            config=gtypes.GenerateContentConfig(
                temperature=0.8,
                max_output_tokens=80,
            ),
        )
        text = (resp.text or "").strip()
        return text or "Hey! What can I help you with today?"
    except Exception:
        return "Hey! What can I help you with today?"
