"""FinPilot — optional natural-language narration layer.

Turns the engine's structured action plan into a friendly paragraph.

* If an API key is configured (``st.secrets`` or env: ``GEMINI_API_KEY`` /
  ``GOOGLE_API_KEY``), it calls Google **Gemini** (``gemini-1.5-flash``) with the
  plan as the ONLY source of truth.
* If no key is present — or the SDK is missing, or the call fails for any reason —
  it returns a deterministic plain-English summary built straight from the plan.

The app works perfectly with no key and no SDK installed. This module imports no
third-party package at load time; the Gemini SDK is imported lazily, only when a key
is actually present.

Security
--------
The API key is **never hardcoded**. It is read only from ``st.secrets`` or the
environment. Narration is presentation only — it restates the engine's plan and
never invents numbers or advice.

Provider note: Gemini was chosen because the optional AI layer can run on a free tier,
which suits a bootstrapped hackathon build. Swapping providers means editing only
``_narrate_with_llm`` below.
"""

from __future__ import annotations

import os

import engine

# Sent to the model as the system instruction. Mirrors the brief verbatim.
SYSTEM_PROMPT = (
    "You are FinPilot's coach. Explain ONLY the provided plan in plain language. "
    "Do not invent numbers or advice."
)

MODEL_NAME = "gemini-1.5-flash"

# Env/secret keys checked, in order.
_KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


# ---------------------------------------------------------------------------
# Key discovery (st.secrets -> env). Never hardcoded.
# ---------------------------------------------------------------------------
def _get_api_key() -> str | None:
    """Return the first API key found in ``st.secrets`` or the environment, else None."""
    # Streamlit secrets first. Accessing st.secrets with no secrets file raises, so guard it.
    try:
        import streamlit as st

        for name in _KEY_NAMES:
            try:
                value = st.secrets.get(name)  # type: ignore[attr-defined]
            except Exception:
                value = None
            if value:
                return str(value)
    except Exception:
        pass  # streamlit not installed or no secrets context — fall through to env.

    for name in _KEY_NAMES:
        value = os.environ.get(name)
        if value:
            return value
    return None


def ai_enabled() -> bool:
    """True when an API key is available (so the UI can label the narration source)."""
    return _get_api_key() is not None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def narrate(
    plan: list[engine.Recommendation], profile: engine.FinancialProfile
) -> str:
    """Return a friendly paragraph explaining ``plan``.

    Uses the LLM when a key is configured and the call succeeds; otherwise falls back
    to a deterministic summary. Never raises — the UI can call this unconditionally.
    """
    key = _get_api_key()
    if key:
        text = _narrate_with_llm(plan, profile, key)
        if text:
            return text
    return _deterministic_summary(plan, profile)


# ---------------------------------------------------------------------------
# LLM path (lazy SDK import; any failure -> None so callers fall back)
# ---------------------------------------------------------------------------
def _serialize_plan(
    plan: list[engine.Recommendation], profile: engine.FinancialProfile
) -> str:
    """Build a compact, factual context string from the plan — the only facts the
    model is allowed to use. No figures beyond what the engine already computed."""
    if not plan:
        return (
            "PLAN (the only facts you may use):\n"
            "No action items. The user's finances are on track: they cover their "
            "expenses, keep debt in check, and are saving toward their goals."
        )
    lines = ["PLAN (the only facts you may use):"]
    for i, rec in enumerate(plan, start=1):
        numbers = "; ".join(f"{k}={v}" for k, v in rec.triggers.items())
        lines.append(
            f"{i}. [{rec.rule_id}] {rec.title} (priority {rec.priority})\n"
            f"   reason: {rec.reason}\n"
            f"   numbers: {numbers}\n"
            f"   impact: {rec.projected_impact}"
        )
    return "\n".join(lines)


def _narrate_with_llm(
    plan: list[engine.Recommendation],
    profile: engine.FinancialProfile,
    key: str,
) -> str | None:
    """Call Gemini with the plan as context. Returns None on ANY failure (missing
    SDK, network error, API change) so ``narrate`` falls back gracefully."""
    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            MODEL_NAME, system_instruction=SYSTEM_PROMPT
        )
        prompt = (
            _serialize_plan(plan, profile)
            + "\n\nWrite one warm, encouraging paragraph (about 4-6 sentences) "
            "explaining this plan to the user in plain language. Use only the "
            "numbers above; do not add new figures or advice."
        )
        response = model.generate_content(prompt)
        text = (getattr(response, "text", "") or "").strip()
        return text or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Deterministic fallback (pure, offline; derived only from the plan)
# ---------------------------------------------------------------------------
def _deterministic_summary(
    plan: list[engine.Recommendation], profile: engine.FinancialProfile
) -> str:
    """Plain-English summary built straight from the plan — no LLM, no network."""
    if not plan:
        return (
            "Good news — your finances look healthy right now. You're covering your "
            "expenses, keeping debt under control, and on track with your savings, so "
            "there are no urgent actions. Keep it up, and revisit this if your income "
            "or expenses change. (Educational estimate, not financial advice.)"
        )

    parts = ["Here's your plan in plain English:"]
    for i, rec in enumerate(plan, start=1):
        parts.append(f"{i}. {rec.title}. {rec.reason} {rec.projected_impact}")
    parts.append(
        "These steps are ordered by urgency (1 = most important). Remember, these are "
        "educational estimates, not financial advice."
    )
    return "\n\n".join(parts)
