"""Unit tests for the FinPilot decision engine.

Coverage goals: every rule firing AND not firing, boundary values (exactly the
threshold), the awkward edges (zero income, no debts, unreachable goal,
``goal_months == 0``), and one ``simulate`` delta case. Tests follow Arrange-Act-
Assert and assert on the exact trigger numbers, not just that a rule fired.
"""

import math

import pytest

import engine
from engine import (
    Debt,
    FinancialProfile,
    debt_to_income,
    emergency_fund_months,
    generate_action_plan,
    goal_feasibility,
    highest_apr_debt,
    monthly_surplus,
    savings_rate,
    simulate,
    simulate_scenario,
    without_debt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def healthy(**overrides) -> FinancialProfile:
    """A deliberately healthy profile (no rule fires). Override one knob per test.

    income 5000, expenses 2500 -> surplus 2500, savings rate 50%, emergency fund
    covers exactly 3.0 months, no debts, no goal.
    """
    base = dict(
        monthly_income=5000.0,
        fixed_expenses=2000.0,
        variable_expenses=500.0,
        current_savings=10000.0,
        emergency_fund=7500.0,  # 3 * 2500
        debts=[],
        goal_amount=0.0,
        goal_months=0,
    )
    base.update(overrides)
    return FinancialProfile(**base)


def fired_ids(profile: FinancialProfile) -> set[str]:
    return {rec.rule_id for rec in generate_action_plan(profile)}


def rec_by_id(profile: FinancialProfile, rule_id: str) -> engine.Recommendation:
    for rec in generate_action_plan(profile):
        if rec.rule_id == rule_id:
            return rec
    raise AssertionError(f"{rule_id} did not fire")


# ---------------------------------------------------------------------------
# Validation & safe math
# ---------------------------------------------------------------------------
def test_baseline_profile_fires_nothing():
    # Arrange / Act
    plan = generate_action_plan(healthy())
    # Assert
    assert plan == []


@pytest.mark.parametrize(
    "field",
    [
        "monthly_income",
        "fixed_expenses",
        "variable_expenses",
        "current_savings",
        "emergency_fund",
        "goal_amount",
    ],
)
def test_negative_numeric_field_rejected(field):
    with pytest.raises(ValueError):
        healthy(**{field: -1.0})


def test_negative_goal_months_rejected():
    with pytest.raises(ValueError):
        healthy(goal_months=-1)


def test_negative_debt_balance_rejected_via_dict():
    with pytest.raises(ValueError):
        healthy(debts=[{"name": "Card", "balance": -100, "apr": 20, "min_payment": 25}])


def test_negative_debt_apr_rejected():
    with pytest.raises(ValueError):
        Debt(name="Card", balance=100.0, apr=-1.0, min_payment=10.0)


def test_empty_debt_name_rejected():
    with pytest.raises(ValueError):
        Debt(name="   ", balance=100.0, apr=20.0, min_payment=10.0)


def test_debt_dict_missing_key_rejected():
    with pytest.raises(ValueError):
        healthy(debts=[{"name": "Card", "balance": 100, "apr": 20}])  # no min_payment


def test_zero_income_is_safe_no_divide_by_zero():
    # Arrange: no income, some expenses.
    profile = FinancialProfile(
        monthly_income=0.0,
        fixed_expenses=500.0,
        variable_expenses=200.0,
        current_savings=0.0,
        emergency_fund=0.0,
    )
    # Act / Assert: metrics return safe values, nothing raises.
    assert savings_rate(profile) == 0.0
    assert debt_to_income(profile) == 0.0
    assert emergency_fund_months(profile) == 0.0  # 0 fund / 700 expenses
    assert monthly_surplus(profile) == pytest.approx(-700.0)
    # And a full plan still generates as a sorted list.
    plan = generate_action_plan(profile)
    assert isinstance(plan, list)
    assert [r.priority for r in plan] == sorted(r.priority for r in plan)


def test_zero_income_with_debt_gives_infinite_dti():
    profile = FinancialProfile(
        monthly_income=0.0,
        fixed_expenses=0.0,
        variable_expenses=0.0,
        current_savings=0.0,
        emergency_fund=0.0,
        debts=[{"name": "Card", "balance": 1000, "apr": 10, "min_payment": 50}],
    )
    assert math.isinf(debt_to_income(profile))


# ---------------------------------------------------------------------------
# Metric correctness on a known fixture
# ---------------------------------------------------------------------------
def test_metrics_on_known_profile():
    # Arrange
    profile = FinancialProfile(
        monthly_income=4000.0,
        fixed_expenses=1500.0,
        variable_expenses=500.0,
        current_savings=3000.0,
        emergency_fund=2000.0,
        debts=[{"name": "Card", "balance": 2000, "apr": 24, "min_payment": 100}],
        goal_amount=6000.0,
        goal_months=10,
    )
    # Act / Assert
    assert monthly_surplus(profile) == pytest.approx(1900.0)        # 4000 - (1500+500+100)
    assert savings_rate(profile) == pytest.approx(0.475)            # 1900 / 4000
    assert emergency_fund_months(profile) == pytest.approx(2000 / 2100)
    assert debt_to_income(profile) == pytest.approx(0.025)          # 100 / 4000
    assert highest_apr_debt(profile).name == "Card"

    feas = goal_feasibility(profile)
    assert feas["remaining"] == pytest.approx(3000.0)              # 6000 - 3000
    assert feas["required_monthly"] == pytest.approx(300.0)        # 3000 / 10
    assert feas["on_track"] is True                               # 1900 surplus >= 300


# ---------------------------------------------------------------------------
# R1 — emergency buffer
# ---------------------------------------------------------------------------
def test_r1_fires_below_three_months():
    profile = healthy(emergency_fund=3000.0)  # 3000 / 2500 = 1.2 months
    rec = rec_by_id(profile, "R1")
    assert rec.priority == 3
    assert rec.triggers["current_months"] == pytest.approx(1.2)
    assert rec.triggers["target_amount"] == pytest.approx(7500.0)
    assert rec.triggers["gap"] == pytest.approx(4500.0)


def test_r1_does_not_fire_at_exactly_three_months():
    # healthy() has emergency_fund 7500 == 3 * 2500 expenses -> exactly 3.0 months.
    assert "R1" not in fired_ids(healthy())


def test_r1_does_not_fire_above_three_months():
    assert "R1" not in fired_ids(healthy(emergency_fund=9000.0))


# ---------------------------------------------------------------------------
# R2 — high-APR debt avalanche
# ---------------------------------------------------------------------------
def test_r2_fires_above_threshold_and_picks_highest_apr():
    profile = healthy(
        debts=[
            {"name": "Loan", "balance": 5000, "apr": 9, "min_payment": 100},
            {"name": "Visa", "balance": 3000, "apr": 22, "min_payment": 80},
        ]
    )
    rec = rec_by_id(profile, "R2")
    assert rec.priority == 2
    assert rec.triggers["debt_name"] == "Visa"  # highest APR, not the larger balance
    assert rec.triggers["apr"] == 22
    # Surplus exists, so the avalanche saves real interest vs minimums.
    assert rec.triggers["interest_saved"] > 0


def test_r2_does_not_fire_at_exactly_fifteen_percent():
    profile = healthy(
        debts=[{"name": "Visa", "balance": 3000, "apr": 15.0, "min_payment": 80}]
    )
    assert "R2" not in fired_ids(profile)


def test_r2_absent_with_no_debts_and_helper_returns_none():
    assert "R2" not in fired_ids(healthy())
    assert highest_apr_debt(healthy()) is None


# ---------------------------------------------------------------------------
# R3 — negative cash flow
# ---------------------------------------------------------------------------
def test_r3_fires_on_deficit_and_flags_largest_cut():
    profile = healthy(fixed_expenses=3000.0, variable_expenses=2800.0)  # outflow 5800 > 5000
    rec = rec_by_id(profile, "R3")
    assert rec.priority == 1
    assert rec.triggers["surplus"] == pytest.approx(-800.0)
    assert rec.triggers["deficit"] == pytest.approx(800.0)
    # Fixed (3000) is larger than variable (2800) here, so it's flagged first.
    assert rec.triggers["largest_cuttable_category"] == "fixed expenses"


def test_r3_does_not_fire_when_positive():
    assert "R3" not in fired_ids(healthy())


# ---------------------------------------------------------------------------
# R4 — savings rate
# ---------------------------------------------------------------------------
def test_r4_fires_below_target():
    # income 5000, outflow 4100 -> surplus 900 -> rate 0.18 < 0.20
    profile = healthy(fixed_expenses=4100.0, variable_expenses=0.0)
    rec = rec_by_id(profile, "R4")
    assert rec.priority == 4
    assert rec.triggers["current_rate"] == pytest.approx(0.18)
    assert rec.triggers["target_dollars"] == pytest.approx(1000.0)
    assert rec.triggers["dollar_gap"] == pytest.approx(100.0)


def test_r4_does_not_fire_at_exactly_twenty_percent():
    # income 5000, outflow 4000 -> surplus 1000 -> rate exactly 0.20
    profile = healthy(fixed_expenses=4000.0, variable_expenses=0.0)
    assert "R4" not in fired_ids(profile)


# ---------------------------------------------------------------------------
# R5 — goal feasibility
# ---------------------------------------------------------------------------
def test_r5_fires_when_off_track():
    # need 12000 in 12 months from 0 saved -> 1000/mo required, surplus only 200.
    profile = healthy(
        fixed_expenses=4300.0,
        variable_expenses=500.0,  # surplus 200
        current_savings=0.0,
        goal_amount=12000.0,
        goal_months=12,
    )
    rec = rec_by_id(profile, "R5")
    assert rec.priority == 5
    assert rec.triggers["required_monthly"] == pytest.approx(1000.0)
    assert rec.triggers["actual_monthly"] == pytest.approx(200.0)
    assert rec.triggers["shortfall"] == pytest.approx(800.0)


def test_r5_does_not_fire_when_on_track():
    profile = healthy(
        current_savings=0.0, goal_amount=1200.0, goal_months=12  # 100/mo vs 2500 surplus
    )
    assert "R5" not in fired_ids(profile)


def test_r5_unreachable_goal_reports_infinite_horizon_without_crashing():
    # Deficit AND a goal -> surplus negative, goal can never be funded.
    profile = healthy(
        fixed_expenses=4000.0,
        variable_expenses=2000.0,  # outflow 6000 > 5000 income -> surplus -1000
        current_savings=0.0,
        goal_amount=5000.0,
        goal_months=10,
    )
    rec = rec_by_id(profile, "R5")
    assert math.isinf(rec.triggers["months_at_current_rate"])
    assert rec.triggers["shortfall"] > 0


def test_r5_absent_when_goal_months_zero():
    profile = healthy(goal_amount=5000.0, goal_months=0)
    assert goal_feasibility(profile)["has_goal"] is False
    assert "R5" not in fired_ids(profile)


def test_r5_absent_when_goal_amount_zero():
    assert "R5" not in fired_ids(healthy(goal_amount=0.0, goal_months=12))


# ---------------------------------------------------------------------------
# Plan ordering & multi-rule
# ---------------------------------------------------------------------------
def test_plan_is_sorted_by_priority():
    # A messy profile that trips several rules at once.
    profile = healthy(
        fixed_expenses=4000.0,
        variable_expenses=2000.0,           # deficit -> R3
        emergency_fund=100.0,               # thin buffer -> R1
        current_savings=0.0,
        goal_amount=10000.0,
        goal_months=10,                     # off track -> R5
        debts=[{"name": "Visa", "balance": 4000, "apr": 25, "min_payment": 120}],  # R2
    )
    plan = generate_action_plan(profile)
    priorities = [rec.priority for rec in plan]
    assert priorities == sorted(priorities)
    assert plan[0].rule_id == "R3"  # negative cash flow is always most urgent


# ---------------------------------------------------------------------------
# simulate — what-if deltas
# ---------------------------------------------------------------------------
def test_simulate_income_bump_resolves_negative_cash_flow():
    # Arrange: a profile in deficit (R3 fires).
    profile = healthy(fixed_expenses=4000.0, variable_expenses=2000.0)  # surplus -1000
    assert "R3" in fired_ids(profile)

    # Act: raise income enough to flip surplus positive.
    result = simulate(profile, monthly_income=8000.0)

    # Assert: surplus improved and R3 is now resolved.
    assert result.deltas["surplus"] > 0
    assert result.scenario_metrics["surplus"] == pytest.approx(2000.0)
    assert "R3" in result.resolved_rules
    assert "R3" not in {rec.rule_id for rec in result.scenario_plan}


def test_simulate_debt_payoff_resolves_high_apr_rule():
    # Arrange: high-APR debt drives R2.
    profile = healthy(
        debts=[{"name": "Visa", "balance": 3000, "apr": 25, "min_payment": 90}]
    )
    assert "R2" in fired_ids(profile)

    # Act: pay the card off.
    result = simulate(profile, debts=without_debt(profile, "Visa").debts)

    # Assert.
    assert "R2" in result.resolved_rules
    assert result.scenario_metrics["total_min_debt_payments"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# simulate_scenario — UI knob -> override mapping (all scenario math in engine)
# ---------------------------------------------------------------------------
def test_scenario_neutral_knobs_equal_baseline():
    profile = healthy(
        fixed_expenses=4300.0,
        variable_expenses=500.0,
        emergency_fund=1000.0,  # trips R1 so the baseline plan is non-empty
    )
    result = simulate_scenario(profile)
    assert result.scenario == profile
    assert all(d == 0 for d in result.deltas.values() if not isinstance(d, bool))
    assert result.resolved_rules == []
    assert result.new_rules == []


def test_scenario_income_pct_scales_income_and_lifts_surplus():
    profile = healthy()  # income 5000, surplus 2500
    result = simulate_scenario(profile, income_pct=10)
    assert result.scenario.monthly_income == pytest.approx(5500.0)
    assert result.deltas["surplus"] == pytest.approx(500.0)


def test_scenario_discretionary_cut_halves_variable():
    profile = healthy(variable_expenses=800.0)
    result = simulate_scenario(profile, discretionary_cut_pct=50)
    assert result.scenario.variable_expenses == pytest.approx(400.0)
    assert result.deltas["surplus"] == pytest.approx(400.0)  # freed-up spending


def test_scenario_payoff_debt_removes_it():
    profile = healthy(
        debts=[
            {"name": "Visa", "balance": 3000, "apr": 25, "min_payment": 90},
            {"name": "Auto", "balance": 8000, "apr": 6, "min_payment": 200},
        ]
    )
    result = simulate_scenario(profile, payoff_debt="Visa")
    names = [d.name for d in result.scenario.debts]
    assert names == ["Auto"]
    assert "R2" in result.resolved_rules  # high-APR card clears once Visa is gone


def test_scenario_income_cannot_go_negative():
    profile = healthy()
    result = simulate_scenario(profile, income_pct=-150)  # would be negative if unclamped
    assert result.scenario.monthly_income == 0.0


# ---------------------------------------------------------------------------
# Amortization helpers (private but worth pinning down)
# ---------------------------------------------------------------------------
def test_months_to_payoff_infinite_when_payment_below_interest():
    # 1000 @ 24% APR -> 20/mo interest; paying 5/mo never amortizes.
    rate = 24 / 100 / 12
    assert math.isinf(engine._months_to_payoff(1000.0, 5.0, rate))


def test_months_to_payoff_finite_for_real_payment():
    rate = 24 / 100 / 12
    months = engine._months_to_payoff(1000.0, 100.0, rate)
    assert math.isfinite(months)
    assert months > 0
