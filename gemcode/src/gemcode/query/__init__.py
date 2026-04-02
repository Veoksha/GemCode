"""
Query-layer types and helpers (clean-room analogue of Claude Code `src/query/*`).

- `transitions` — terminal vs continue reasons for the model↔tool loop.
- `config` — immutable gate snapshot per run (env/session).
- `token_budget` — continuation/stop decisions vs a per-turn token budget.
- `deps` — injectable dependencies for tests.
- `stop_hooks` — optional post-turn subprocess hooks.
- `engine` — `GemCodeQueryEngine` facade (outer session + submit message).

The ADK `Runner` still executes the inner loop; these modules document parity and
host logic that maps to `query.ts` / `QueryEngine.ts` responsibilities.
"""

from gemcode.query.config import QueryGates, build_query_gates
from gemcode.query.token_budget import (
  BudgetTracker,
  TokenBudgetDecision,
  check_token_budget,
  get_budget_continuation_message,
)
from gemcode.query.transitions import Continue, Terminal

# Note: import `GemCodeQueryEngine` from `gemcode.query.engine` to avoid import cycles
# (engine pulls session_runtime → agent → callbacks → query.token_budget).

__all__ = [
  "BudgetTracker",
  "Continue",
  "QueryGates",
  "Terminal",
  "TokenBudgetDecision",
  "build_query_gates",
  "check_token_budget",
  "get_budget_continuation_message",
]
