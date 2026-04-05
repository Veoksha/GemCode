"""
Parallel code-review pipeline using ADK SequentialAgent + ParallelAgent.

Inspired by the ADK community financial-advisor pattern:
  ParallelAgent (3 specialist reviewers run simultaneously)
    → SecurityReviewer   (writes to state["security_findings"])
    → StyleReviewer      (writes to state["style_findings"])
    → CorrectnessReviewer(writes to state["correctness_findings"])
  → SynthesisAgent (reads all three state keys and produces final report)

The pipeline is wrapped in a SequentialAgent:
  ParallelReviewers → Synthesizer

Called from the /review slash command with an optional scope argument:
  /review            — review recent git diff (staged + unstaged changes)
  /review src/auth/  — review a specific directory
  /review file.py    — review a specific file
"""

from __future__ import annotations

from typing import Any

_GEMINI_FLASH = "gemini-2.5-flash"
_GEMINI_PRO = "gemini-2.5-pro"


def build_review_pipeline(model: str | None = None) -> Any:
  """
  Build and return a SequentialAgent [ParallelReviewers → Synthesizer].

  The pipeline is stateless — pass the diff/files as the initial prompt.
  Uses flash for the three parallel reviewers (fast + cheap) and the
  provided model (or flash) for the final synthesis.
  """
  try:
    from google.adk.agents import LlmAgent, SequentialAgent, ParallelAgent
  except ImportError as e:
    raise ImportError("google-adk is required for /review") from e

  synthesizer_model = model or _GEMINI_FLASH

  security_reviewer = LlmAgent(
      name="SecurityReviewer",
      model=_GEMINI_FLASH,
      include_contents="none",  # Stateless — only sees what's injected via state
      instruction="""You are a security-focused code reviewer.
Review the code changes provided in the message. Focus exclusively on:
- Authentication/authorization bypasses
- Injection vulnerabilities (SQL, command, path traversal)
- Secrets or credentials in code
- Insecure deserialization or parsing
- Missing input validation or sanitization
- Race conditions affecting security
- Dangerous use of eval/exec/subprocess

For each issue: SEVERITY (Critical/High/Medium/Low), FILE+LINE if visible, description, fix suggestion.
If no security issues found, say "No security issues found."
Be concise — max 300 words. Output ONLY your security findings.""",
      output_key="security_findings",
  )

  style_reviewer = LlmAgent(
      name="StyleReviewer",
      model=_GEMINI_FLASH,
      include_contents="none",
      instruction="""You are a code style and readability reviewer.
Review the code changes provided in the message. Focus exclusively on:
- Code readability and clarity
- Naming conventions (variables, functions, classes)
- DRY violations and unnecessary duplication
- Function/method length and complexity
- Missing or poor documentation/comments
- Inconsistent formatting or style
- Dead code or unused imports

For each issue: SEVERITY (High/Medium/Low), FILE+LINE if visible, description, suggestion.
If no style issues found, say "No style issues found."
Be concise — max 300 words. Output ONLY your style findings.""",
      output_key="style_findings",
  )

  correctness_reviewer = LlmAgent(
      name="CorrectnessReviewer",
      model=_GEMINI_FLASH,
      include_contents="none",
      instruction="""You are a correctness-focused code reviewer.
Review the code changes provided in the message. Focus exclusively on:
- Logic errors and off-by-one bugs
- Null/undefined pointer dereferences
- Incorrect error handling or exception swallowing
- Type mismatches or coercion issues
- Missing edge case handling
- Resource leaks (file handles, connections, memory)
- Incorrect algorithm implementation
- Broken tests or missing test coverage

For each issue: SEVERITY (Critical/High/Medium/Low), FILE+LINE if visible, description, fix suggestion.
If no correctness issues found, say "No correctness issues found."
Be concise — max 300 words. Output ONLY your correctness findings.""",
      output_key="correctness_findings",
  )

  synthesizer = LlmAgent(
      name="ReviewSynthesizer",
      model=synthesizer_model,
      include_contents="none",
      instruction="""You are a senior engineering lead synthesizing a code review.

You have three specialist review reports in state:
- Security findings: {security_findings}
- Style findings:   {style_findings}
- Correctness:      {correctness_findings}

Produce a clear, actionable code review report:

## Code Review Summary

### Critical / Must Fix
[List only Critical and High severity issues from all reviewers, grouped by file if possible]

### Suggestions
[List Medium/Low severity issues worth addressing, grouped by category]

### Verdict
[One of: APPROVE / APPROVE WITH MINOR CHANGES / REQUEST CHANGES — with 1-2 sentence justification]

Keep the report under 500 words. Be direct and developer-friendly.""",
  )

  parallel_reviewers = ParallelAgent(
      name="ParallelReviewers",
      sub_agents=[security_reviewer, style_reviewer, correctness_reviewer],
  )

  pipeline = SequentialAgent(
      name="CodeReviewPipeline",
      description="Parallel code review: security + style + correctness, then synthesis.",
      sub_agents=[parallel_reviewers, synthesizer],
  )

  return pipeline
