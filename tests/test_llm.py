"""Tests for the optional narration layer.

These are fully offline: with no API key configured, ``narrate`` never enters the LLM
branch, so nothing here touches the network or the Gemini SDK. They prove the
deterministic fallback works and that the public API never raises.
"""

import pytest

import engine
import llm


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """Guarantee no key is visible, so every test exercises the deterministic path."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _profile_with_issues() -> engine.FinancialProfile:
    # Thin emergency fund (R1) + a high-APR card (R2).
    return engine.FinancialProfile(
        monthly_income=5200.0,
        fixed_expenses=2100.0,
        variable_expenses=900.0,
        current_savings=4000.0,
        emergency_fund=500.0,
        debts=[{"name": "Visa", "balance": 4200.0, "apr": 24.99, "min_payment": 120.0}],
        goal_amount=0.0,
        goal_months=0,
    )


def _healthy_profile() -> engine.FinancialProfile:
    return engine.FinancialProfile(
        monthly_income=5000.0,
        fixed_expenses=2000.0,
        variable_expenses=500.0,
        current_savings=10000.0,
        emergency_fund=7500.0,  # exactly 3 months
    )


def test_ai_disabled_without_key():
    assert llm.ai_enabled() is False


def test_ai_enabled_reads_env_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-not-used-offline")
    assert llm.ai_enabled() is True


def test_narrate_empty_plan_returns_healthy_message():
    profile = _healthy_profile()
    plan = engine.generate_action_plan(profile)
    assert plan == []

    text = llm.narrate(plan, profile)
    assert isinstance(text, str) and text.strip()
    assert "healthy" in text.lower()
    assert "not financial advice" in text.lower()


def test_narrate_includes_every_recommendation_title():
    profile = _profile_with_issues()
    plan = engine.generate_action_plan(profile)
    assert {rec.rule_id for rec in plan} == {"R1", "R2"}

    text = llm.narrate(plan, profile)
    for rec in plan:
        assert rec.title in text  # every action is mentioned by name


def test_narrate_never_raises_even_with_odd_profile():
    profile = engine.FinancialProfile(
        monthly_income=0.0,
        fixed_expenses=500.0,
        variable_expenses=200.0,
        current_savings=0.0,
        emergency_fund=0.0,
    )
    plan = engine.generate_action_plan(profile)
    # Should produce a string, not throw, regardless of plan contents.
    assert isinstance(llm.narrate(plan, profile), str)


def test_serialize_plan_uses_only_plan_facts():
    profile = _profile_with_issues()
    plan = engine.generate_action_plan(profile)

    context = llm._serialize_plan(plan, profile)
    # Contains the rule ids that fired...
    assert "[R1]" in context and "[R2]" in context
    # ...and is explicitly framed as the only allowed facts.
    assert "the only facts you may use" in context.lower()


def test_serialize_plan_empty():
    profile = _healthy_profile()
    context = llm._serialize_plan([], profile)
    assert "on track" in context.lower()
