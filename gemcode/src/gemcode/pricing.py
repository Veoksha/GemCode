"""
Per-model token pricing for cost estimation.

All prices are USD per 1 million tokens (input / output separately).
Sources: Google AI Studio / Vertex AI pricing pages as of 2026.

Usage:
    from gemcode.pricing import estimate_cost
    usd = estimate_cost("gemini-2.5-flash", input_tokens=5000, output_tokens=1200)
"""

from __future__ import annotations

# Format: model_prefix → (input_$/M, output_$/M)
# Matches are done by checking if the model id STARTS WITH any key (longest first).
_PRICING_TABLE: dict[str, tuple[float, float]] = {
    # ── Gemini 2.5 ──────────────────────────────────────────────────────────
    "gemini-2.5-pro":         (3.50, 10.50),   # standard context ≤200k
    "gemini-2.5-flash-lite":  (0.10,  0.40),   # lowest-cost 2.5 (must precede flash)
    "gemini-2.5-flash":       (0.15,  0.60),   # standard context
    "gemini-2.5-flash-8b":    (0.037, 0.15),   # 8B lite variant
    # ── Gemini 2.0 ──────────────────────────────────────────────────────────
    "gemini-2.0-flash":       (0.10,  0.40),
    "gemini-2.0-flash-lite":  (0.075, 0.30),
    "gemini-2.0-pro":         (3.50, 10.50),
    # ── Gemini 1.5 ──────────────────────────────────────────────────────────
    "gemini-1.5-flash":       (0.075, 0.30),
    "gemini-1.5-pro":         (1.25,  5.00),
    # ── Gemini experimental / preview ───────────────────────────────────────
    "gemini-exp":             (0.00,  0.00),   # free during preview
    "gemini-3":               (0.30,  1.20),   # approximate future pricing
}


def estimate_cost(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float | None:
    """
    Return estimated USD cost for a model turn, or None if pricing is unknown.

    Args:
        model: Model id string (e.g. "gemini-2.5-flash").
        input_tokens: Prompt/input token count.
        output_tokens: Candidates/output token count.
    """
    if not model:
        return None
    model_lower = model.lower().strip()
    # Match longest prefix first for specificity.
    for prefix in sorted(_PRICING_TABLE, key=len, reverse=True):
        if model_lower.startswith(prefix):
            in_rate, out_rate = _PRICING_TABLE[prefix]
            return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    return None


def format_cost(usd: float | None) -> str:
    """Human-readable cost string, e.g. '$0.0012' or '$1.23'."""
    if usd is None:
        return ""
    if usd < 0.0001:
        return f"<$0.0001"
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.3f}"


def format_tokens(n: int) -> str:
    """Format a token count compactly: 1234 → '1.2k', 12345678 → '12.3M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)
