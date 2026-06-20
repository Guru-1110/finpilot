"""FinPilot core decision engine.

A transparent, "glass-box" personal-finance coach. Every recommendation exposes
the rule that fired, the exact numbers that triggered it, and a projected impact —
so a user can audit *why* the coach is telling them something, not just *what*.

This module is intentionally pure Python: it imports nothing third-party (no
Streamlit), so the decision logic can be unit-tested in isolation before any UI is
layered on top later.

Conventions
-----------
* All monetary values are monthly amounts in the user's currency units.
* Debt ``apr`` is expressed in **percent points** (e.g. ``18.5`` means 18.5%).
* Dataclasses are frozen (immutable). "What-if" scenarios build new copies via
  :func:`dataclasses.replace` rather than mutating in place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

# ---------------------------------------------------------------------------
# Tunable thresholds — named constants so the rules read like policy, not magic.
# ---------------------------------------------------------------------------
EMERGENCY_FUND_MIN_MONTHS: float = 3.0   # a buffer should cover at least this many months
HIGH_APR_THRESHOLD: float = 15.0         # percent points; strictly above this -> avalanche
TARGET_SAVINGS_RATE: float = 0.20        # save at least 20% of income
MONTHS_PER_YEAR: int = 12


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _require_non_negative(**values: float) -> None:
    """Raise ``ValueError`` if any keyword value is not a finite, non-negative number.

    ``bool`` is rejected explicitly because it is a subclass of ``int`` and would
    otherwise sneak through as ``0``/``1``.
    """
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number, got {value!r}")
        if math.isnan(value):
            raise ValueError(f"{name} must not be NaN")
        if value < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Debt:
    """A single debt line item.

    Parameters
    ----------
    name:
        Human-readable label, e.g. ``"Visa"``.
    balance:
        Outstanding principal (>= 0).
    apr:
        Annual percentage rate in **percent points** (``18.5`` == 18.5%).
    min_payment:
        Required minimum monthly payment (>= 0).
    """

    name: str
    balance: float
    apr: float
    min_payment: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Debt.name must be a non-empty string")
        _require_non_negative(
            balance=self.balance, apr=self.apr, min_payment=self.min_payment
        )

    @property
    def monthly_rate(self) -> float:
        """Monthly interest rate as a decimal fraction (``apr`` percent / 100 / 12)."""
        return self.apr / 100.0 / MONTHS_PER_YEAR


@dataclass(frozen=True)
class Recommendation:
    """One piece of glass-box advice produced by a rule.

    Attributes
    ----------
    title:
        Short headline for the action.
    priority:
        1 = highest urgency. The action plan is sorted by this ascending.
    rule_id:
        Stable identifier of the rule that fired (``"R1"`` ... ``"R5"``).
    reason:
        Human-readable explanation of *why* this fired.
    triggers:
        The exact numbers (and labels) that caused the rule to fire — the audit
        trail behind ``reason``.
    projected_impact:
        What the user gains by acting on the recommendation.
    """

    title: str
    priority: int
    rule_id: str
    reason: str
    triggers: dict[str, float | str]
    projected_impact: str


def _normalize_debts(debts: object) -> tuple[Debt, ...]:
    """Coerce a debts input (``list[dict]`` or ``list[Debt]``) into a tuple of ``Debt``."""
    if debts is None:
        return ()
    normalized: list[Debt] = []
    for item in debts:
        if isinstance(item, Debt):
            normalized.append(item)
        elif isinstance(item, dict):
            try:
                normalized.append(
                    Debt(
                        name=item["name"],
                        balance=item["balance"],
                        apr=item["apr"],
                        min_payment=item["min_payment"],
                    )
                )
            except KeyError as exc:
                raise ValueError(
                    f"debt dict missing required key: {exc.args[0]!r}"
                ) from exc
        else:
            raise ValueError(
                f"each debt must be a dict or Debt, got {type(item).__name__}"
            )
    return tuple(normalized)


@dataclass(frozen=True)
class FinancialProfile:
    """A snapshot of a person's monthly finances plus one savings goal.

    ``debts`` accepts a list of dicts (``{name, balance, apr, min_payment}``) or
    :class:`Debt` objects; either way it is stored as an immutable tuple of
    :class:`Debt`. All numeric fields must be non-negative.
    """

    monthly_income: float
    fixed_expenses: float
    variable_expenses: float
    current_savings: float
    emergency_fund: float
    debts: tuple[Debt, ...] = ()
    goal_amount: float = 0.0
    goal_months: int = 0

    def __post_init__(self) -> None:
        _require_non_negative(
            monthly_income=self.monthly_income,
            fixed_expenses=self.fixed_expenses,
            variable_expenses=self.variable_expenses,
            current_savings=self.current_savings,
            emergency_fund=self.emergency_fund,
            goal_amount=self.goal_amount,
        )
        if isinstance(self.goal_months, bool) or not isinstance(self.goal_months, int):
            raise ValueError("goal_months must be an int")
        if self.goal_months < 0:
            raise ValueError("goal_months must be >= 0")
        # Frozen-safe normalization: validate/convert debts, then store the tuple.
        object.__setattr__(self, "debts", _normalize_debts(self.debts))


@dataclass(frozen=True)
class SimulationResult:
    """The result of a what-if scenario: two plans plus the metric deltas between them."""

    scenario: FinancialProfile
    baseline_metrics: dict[str, float | bool]
    scenario_metrics: dict[str, float | bool]
    deltas: dict[str, float | bool]
    baseline_plan: list[Recommendation]
    scenario_plan: list[Recommendation]
    resolved_rules: list[str]   # fired in baseline, gone in scenario
    new_rules: list[str]        # not in baseline, fired in scenario


# ---------------------------------------------------------------------------
# Pure metric functions (every division guards against zero)
# ---------------------------------------------------------------------------
def total_min_debt_payments(profile: FinancialProfile) -> float:
    """Sum of required minimum payments across all debts."""
    return sum(d.min_payment for d in profile.debts)


def monthly_expenses(profile: FinancialProfile) -> float:
    """Total monthly outflow: fixed + variable + required debt minimums."""
    return (
        profile.fixed_expenses
        + profile.variable_expenses
        + total_min_debt_payments(profile)
    )


def monthly_surplus(profile: FinancialProfile) -> float:
    """Money left each month after all expenses (negative means a deficit).

    Debt minimums are subtracted explicitly so the cash-flow picture is honest and
    does not silently assume they are baked into ``fixed_expenses``.
    """
    return profile.monthly_income - monthly_expenses(profile)


def savings_rate(profile: FinancialProfile) -> float:
    """Surplus as a fraction of income. Returns ``0.0`` when income is zero."""
    if profile.monthly_income <= 0:
        return 0.0
    return monthly_surplus(profile) / profile.monthly_income


def emergency_fund_months(profile: FinancialProfile) -> float:
    """How many months of expenses the emergency fund covers.

    Returns ``inf`` when there are no expenses (a buffer trivially covers nothing).
    """
    expenses = monthly_expenses(profile)
    if expenses <= 0:
        return math.inf
    return profile.emergency_fund / expenses


def debt_to_income(profile: FinancialProfile) -> float:
    """Required debt payments as a fraction of income (a monthly DTI ratio).

    Returns ``inf`` when income is zero but debt payments exist, else ``0.0``.
    """
    payments = total_min_debt_payments(profile)
    if profile.monthly_income <= 0:
        return math.inf if payments > 0 else 0.0
    return payments / profile.monthly_income


def highest_apr_debt(profile: FinancialProfile) -> Debt | None:
    """The debt with the highest APR, or ``None`` when there are no debts."""
    if not profile.debts:
        return None
    return max(profile.debts, key=lambda d: d.apr)


def goal_feasibility(profile: FinancialProfile) -> dict[str, float | bool]:
    """Assess whether the savings goal is reachable at the current surplus.

    Existing ``current_savings`` is credited toward the goal. Returns a dict with
    ``required_monthly`` (to finish on time), ``actual_monthly`` (current surplus),
    ``shortfall``, ``on_track``, ``remaining``, ``months_at_current_rate`` (``inf``
    if surplus <= 0), and ``has_goal``.
    """
    has_goal = profile.goal_amount > 0 and profile.goal_months > 0
    surplus = monthly_surplus(profile)
    remaining = max(profile.goal_amount - profile.current_savings, 0.0)

    required_monthly = remaining / profile.goal_months if has_goal else 0.0
    shortfall = max(required_monthly - surplus, 0.0)
    on_track = surplus >= required_monthly

    if remaining <= 0:
        months_at_current_rate: float = 0.0
    elif surplus > 0:
        months_at_current_rate = remaining / surplus
    else:
        months_at_current_rate = math.inf

    return {
        "has_goal": has_goal,
        "required_monthly": required_monthly,
        "actual_monthly": surplus,
        "shortfall": shortfall,
        "on_track": on_track,
        "remaining": remaining,
        "months_at_current_rate": months_at_current_rate,
    }


# ---------------------------------------------------------------------------
# Amortization helpers (power the R2 "interest saved" figure)
# ---------------------------------------------------------------------------
def _months_to_payoff(balance: float, monthly_payment: float, monthly_rate: float) -> float:
    """Whole months to fully amortize ``balance`` at a fixed monthly payment.

    Returns ``inf`` when the payment cannot cover the first month's interest
    (the balance never reaches zero).
    """
    if balance <= 0:
        return 0.0
    if monthly_payment <= 0:
        return math.inf
    if monthly_rate <= 0:
        return math.ceil(balance / monthly_payment)
    if monthly_payment <= balance * monthly_rate:
        return math.inf
    # Closed-form amortization: n = -ln(1 - r*B/P) / ln(1 + r)
    n = -math.log(1 - (monthly_rate * balance) / monthly_payment) / math.log(1 + monthly_rate)
    return math.ceil(n)


def _total_interest(balance: float, monthly_payment: float, monthly_rate: float) -> float:
    """Approximate total interest paid over the life of the debt (``inf`` if never paid off).

    Estimated as ``payment * months - balance``; the final month is treated as full,
    so this slightly overstates interest — acceptable for a comparison figure.
    """
    months = _months_to_payoff(balance, monthly_payment, monthly_rate)
    if math.isinf(months):
        return math.inf
    if months <= 0:
        return 0.0
    return max(monthly_payment * months - balance, 0.0)


# ---------------------------------------------------------------------------
# Rules — each returns a Recommendation when it fires, else None.
# ---------------------------------------------------------------------------
def _rule_negative_cash_flow(profile: FinancialProfile) -> Recommendation | None:
    """R3: spending more than you earn. Highest priority — stop the bleed first."""
    surplus = monthly_surplus(profile)
    if surplus >= 0:
        return None

    deficit = -surplus
    # Variable spending is the most discretionary, so it is the first thing to cut.
    cuttable = sorted(
        [
            ("variable expenses", profile.variable_expenses),
            ("fixed expenses", profile.fixed_expenses),
        ],
        key=lambda kv: kv[1],
        reverse=True,
    )
    largest_name, largest_amount = cuttable[0]

    return Recommendation(
        title="Stop the monthly bleed",
        priority=1,
        rule_id="R3",
        reason=(
            f"You spend ${deficit:,.0f} more than you earn each month "
            f"(income ${profile.monthly_income:,.0f} vs outflow "
            f"${monthly_expenses(profile):,.0f})."
        ),
        triggers={
            "surplus": surplus,
            "deficit": deficit,
            "monthly_income": profile.monthly_income,
            "fixed_expenses": profile.fixed_expenses,
            "variable_expenses": profile.variable_expenses,
            "min_debt_payments": total_min_debt_payments(profile),
            "largest_cuttable_category": largest_name,
            "largest_cuttable_amount": largest_amount,
        },
        projected_impact=(
            f"Cutting ${deficit:,.0f} — start with {largest_name} "
            f"(currently ${largest_amount:,.0f}) — restores positive cash flow."
        ),
    )


def _rule_high_apr_debt(profile: FinancialProfile) -> Recommendation | None:
    """R2: a debt above the high-APR line. Avalanche the most expensive one first."""
    target = highest_apr_debt(profile)
    if target is None or target.apr <= HIGH_APR_THRESHOLD:
        return None

    extra = max(monthly_surplus(profile), 0.0)  # spare cash to throw at the debt
    rate = target.monthly_rate
    interest_min_only = _total_interest(target.balance, target.min_payment, rate)
    interest_accelerated = _total_interest(target.balance, target.min_payment + extra, rate)

    if math.isinf(interest_min_only) or math.isinf(interest_accelerated):
        interest_saved: float = math.inf
    else:
        interest_saved = max(interest_min_only - interest_accelerated, 0.0)

    if extra > 0 and math.isfinite(interest_saved):
        impact = (
            f"Putting your ${extra:,.0f}/mo surplus toward {target.name} first "
            f"saves about ${interest_saved:,.0f} in interest versus paying minimums."
        )
    elif extra <= 0:
        impact = (
            f"Free up surplus, then attack {target.name} (APR {target.apr:.1f}%) "
            f"first — it is your most expensive debt."
        )
    else:
        impact = (
            f"At the current minimum, {target.name} barely amortizes — increasing the "
            f"payment is the only way to escape the {target.apr:.1f}% APR."
        )

    return Recommendation(
        title=f"Avalanche your highest-APR debt ({target.name})",
        priority=2,
        rule_id="R2",
        reason=(
            f"{target.name} charges {target.apr:.1f}% APR — above the "
            f"{HIGH_APR_THRESHOLD:.0f}% high-interest line — on a "
            f"${target.balance:,.0f} balance. Pay it before any lower-rate debt."
        ),
        triggers={
            "debt_name": target.name,
            "apr": target.apr,
            "balance": target.balance,
            "min_payment": target.min_payment,
            "surplus_applied": extra,
            "interest_saved": interest_saved,
        },
        projected_impact=impact,
    )


def _rule_emergency_buffer(profile: FinancialProfile) -> Recommendation | None:
    """R1: emergency fund covers fewer than the minimum months of expenses."""
    months = emergency_fund_months(profile)
    if months >= EMERGENCY_FUND_MIN_MONTHS:
        return None

    expenses = monthly_expenses(profile)
    target_amount = EMERGENCY_FUND_MIN_MONTHS * expenses
    gap = max(target_amount - profile.emergency_fund, 0.0)
    surplus = monthly_surplus(profile)

    if surplus > 0:
        months_to_reach: float = math.ceil(gap / surplus)
        impact = (
            f"Routing your ${surplus:,.0f}/mo surplus to savings reaches a "
            f"{EMERGENCY_FUND_MIN_MONTHS:.0f}-month buffer "
            f"(~${target_amount:,.0f}) in about {months_to_reach:.0f} month(s)."
        )
    else:
        months_to_reach = math.inf
        impact = (
            f"You need ~${target_amount:,.0f} for a "
            f"{EMERGENCY_FUND_MIN_MONTHS:.0f}-month buffer, but have no surplus to "
            f"fund it — fix cash flow first."
        )

    return Recommendation(
        title="Build a 3-month emergency buffer",
        priority=3,
        rule_id="R1",
        reason=(
            f"Your emergency fund covers only {months:.1f} month(s) of expenses, "
            f"below the {EMERGENCY_FUND_MIN_MONTHS:.0f}-month minimum."
        ),
        triggers={
            "current_months": months,
            "target_months": EMERGENCY_FUND_MIN_MONTHS,
            "monthly_expenses": expenses,
            "target_amount": target_amount,
            "current_fund": profile.emergency_fund,
            "gap": gap,
            "surplus": surplus,
            "months_to_reach": months_to_reach,
        },
        projected_impact=impact,
    )


def _rule_low_savings_rate(profile: FinancialProfile) -> Recommendation | None:
    """R4: saving a smaller share of income than the target rate."""
    if profile.monthly_income <= 0:
        return None  # no income -> no meaningful savings-rate target

    rate = savings_rate(profile)
    if rate >= TARGET_SAVINGS_RATE:
        return None

    target_dollars = TARGET_SAVINGS_RATE * profile.monthly_income
    current_dollars = monthly_surplus(profile)
    gap = max(target_dollars - current_dollars, 0.0)

    return Recommendation(
        title="Lift your savings rate to 20%",
        priority=4,
        rule_id="R4",
        reason=(
            f"You save {rate * 100:.0f}% of income, under the "
            f"{TARGET_SAVINGS_RATE * 100:.0f}% target."
        ),
        triggers={
            "current_rate": rate,
            "target_rate": TARGET_SAVINGS_RATE,
            "monthly_income": profile.monthly_income,
            "target_dollars": target_dollars,
            "current_dollars": current_dollars,
            "dollar_gap": gap,
        },
        projected_impact=(
            f"Saving an extra ${gap:,.0f}/mo (to ${target_dollars:,.0f}) hits the "
            f"{TARGET_SAVINGS_RATE * 100:.0f}% savings target."
        ),
    )


def _rule_goal_off_track(profile: FinancialProfile) -> Recommendation | None:
    """R5: the savings goal is not reachable at the current surplus."""
    feas = goal_feasibility(profile)
    if not feas["has_goal"] or feas["on_track"]:
        return None

    required = feas["required_monthly"]
    actual = feas["actual_monthly"]
    shortfall = feas["shortfall"]
    months_at_rate = feas["months_at_current_rate"]

    if math.isinf(months_at_rate):
        impact = (
            f"At your current rate the goal is unreachable. Find ${shortfall:,.0f}/mo "
            f"more (or extend the timeline) to close the gap."
        )
    else:
        impact = (
            f"You need ${required:,.0f}/mo but free up ${actual:,.0f}/mo — add "
            f"${shortfall:,.0f}/mo to stay on schedule."
        )

    return Recommendation(
        title="Close the gap on your savings goal",
        priority=5,
        rule_id="R5",
        reason=(
            f"Reaching ${profile.goal_amount:,.0f} in {profile.goal_months} months "
            f"needs ${required:,.0f}/mo, but you only free up ${actual:,.0f}/mo."
        ),
        triggers={
            "goal_amount": profile.goal_amount,
            "goal_months": profile.goal_months,
            "required_monthly": required,
            "actual_monthly": actual,
            "shortfall": shortfall,
            "months_at_current_rate": months_at_rate,
        },
        projected_impact=impact,
    )


# Ordered registry of all rules (callable: profile -> Recommendation | None).
_RULES = (
    _rule_negative_cash_flow,
    _rule_high_apr_debt,
    _rule_emergency_buffer,
    _rule_low_savings_rate,
    _rule_goal_off_track,
)


def generate_action_plan(profile: FinancialProfile) -> list[Recommendation]:
    """Evaluate every rule and return the fired recommendations, sorted by priority."""
    fired = [rec for rec in (rule(profile) for rule in _RULES) if rec is not None]
    return sorted(fired, key=lambda rec: rec.priority)


# ---------------------------------------------------------------------------
# Metrics bundle + what-if simulation
# ---------------------------------------------------------------------------
def _compute_metrics(profile: FinancialProfile) -> dict[str, float | bool]:
    """Bundle the headline metrics. Shared by callers and :func:`simulate` (DRY)."""
    feas = goal_feasibility(profile)
    return {
        "surplus": monthly_surplus(profile),
        "monthly_expenses": monthly_expenses(profile),
        "savings_rate": savings_rate(profile),
        "emergency_fund_months": emergency_fund_months(profile),
        "debt_to_income": debt_to_income(profile),
        "total_min_debt_payments": total_min_debt_payments(profile),
        "goal_required_monthly": feas["required_monthly"],
        "goal_shortfall": feas["shortfall"],
        "goal_on_track": feas["on_track"],
    }


def _delta(scenario_value: float | bool, baseline_value: float | bool) -> float | bool:
    """Scenario-minus-baseline delta, tolerating booleans and infinities."""
    if isinstance(scenario_value, bool) or isinstance(baseline_value, bool):
        return scenario_value  # report the new boolean state, not an arithmetic delta
    if math.isinf(scenario_value) or math.isinf(baseline_value):
        # inf - inf is NaN; report 0 when unchanged, else the new value.
        return 0.0 if scenario_value == baseline_value else scenario_value
    return scenario_value - baseline_value


def without_debt(profile: FinancialProfile, name: str) -> FinancialProfile:
    """Return a copy of ``profile`` with the named debt removed (i.e. paid off)."""
    remaining = tuple(d for d in profile.debts if d.name != name)
    return replace(profile, debts=remaining)


def simulate(profile: FinancialProfile, **overrides: object) -> SimulationResult:
    """Run a what-if scenario and compare it against the baseline profile.

    ``overrides`` are applied via :func:`dataclasses.replace` (and re-validated by
    ``FinancialProfile.__post_init__``). Typical scenarios:

    * income change ......... ``simulate(p, monthly_income=6000)``
    * expense cut ........... ``simulate(p, variable_expenses=300)``
    * debt payoff ........... ``simulate(p, debts=without_debt(p, "Visa").debts)``
      (or simply ``simulate(without_debt(p, "Visa"))`` with no overrides)

    Returns a :class:`SimulationResult` carrying both action plans, per-metric
    deltas, and which ``rule_id``s were resolved vs newly triggered.
    """
    scenario = replace(profile, **overrides)

    baseline_metrics = _compute_metrics(profile)
    scenario_metrics = _compute_metrics(scenario)
    deltas = {
        key: _delta(scenario_metrics[key], baseline_metrics[key])
        for key in baseline_metrics
    }

    baseline_plan = generate_action_plan(profile)
    scenario_plan = generate_action_plan(scenario)
    baseline_ids = {rec.rule_id for rec in baseline_plan}
    scenario_ids = {rec.rule_id for rec in scenario_plan}

    return SimulationResult(
        scenario=scenario,
        baseline_metrics=baseline_metrics,
        scenario_metrics=scenario_metrics,
        deltas=deltas,
        baseline_plan=baseline_plan,
        scenario_plan=scenario_plan,
        resolved_rules=sorted(baseline_ids - scenario_ids),
        new_rules=sorted(scenario_ids - baseline_ids),
    )


def simulate_scenario(
    profile: FinancialProfile,
    *,
    income_pct: float = 0.0,
    discretionary_cut_pct: float = 0.0,
    payoff_debt: str | None = None,
) -> SimulationResult:
    """Map UI-style scenario knobs onto engine overrides and run :func:`simulate`.

    This keeps the percentage arithmetic (which a UI would otherwise have to do) inside
    the engine, so callers pass through raw control values and stay free of business logic.

    Parameters
    ----------
    income_pct:
        Percent change to monthly income (``+10`` = +10%, ``-20`` = -20%). Income is
        floored at 0 so a -100% (or worse) knob can never produce a negative income.
    discretionary_cut_pct:
        Percent to trim from ``variable_expenses`` (``20`` = cut 20%). Floored at 0.
    payoff_debt:
        Name of a debt to remove (treated as paid off), or ``None`` to leave debts as-is.

    Returns
    -------
    SimulationResult
        The same rich comparison object returned by :func:`simulate`. With all knobs at
        their neutral defaults the scenario equals the baseline (zero deltas).
    """
    overrides: dict[str, object] = {}
    if income_pct:
        overrides["monthly_income"] = max(
            profile.monthly_income * (1 + income_pct / 100), 0.0
        )
    if discretionary_cut_pct:
        overrides["variable_expenses"] = max(
            profile.variable_expenses * (1 - discretionary_cut_pct / 100), 0.0
        )
    if payoff_debt:
        overrides["debts"] = tuple(d for d in profile.debts if d.name != payoff_debt)
    return simulate(profile, **overrides)
