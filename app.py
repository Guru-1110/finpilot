"""FinPilot — Streamlit UI for the glass-box finance coach.

This module is the PRESENTATION layer only. It contains **zero business/financial
logic**: every metric, rule, plan, and what-if calculation comes from ``engine.py``.
The code here only collects input, calls the engine, and formats results. (Display
formatting — currency commas, ``x100`` for percent, ``isinf`` checks — is presentation,
not business logic.)

Run with:  ``streamlit run app.py``
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

import engine
import llm

# Page config must be the first Streamlit call.
st.set_page_config(page_title="FinPilot", page_icon="🧭", layout="wide")

DISCLAIMER = "FinPilot is an educational tool, not licensed financial advice."

# ---------------------------------------------------------------------------
# Sample profile (for the "Load sample profile" button and the first-run state).
# Chosen to trip a couple of rules so the demo has something to show: a thin
# emergency fund (R1) and a high-APR card (R2).
# ---------------------------------------------------------------------------
SAMPLE_PROFILE = {
    "income": 5200.0,
    "fixed": 2100.0,
    "variable": 900.0,
    "savings": 4000.0,
    "ef": 2500.0,
    "goal_amount": 15000.0,
    "goal_months": 18,
}
SAMPLE_DEBTS = [
    {"name": "Visa", "balance": 4200.0, "apr": 24.99, "min_payment": 120.0},
    {"name": "Student loan", "balance": 11000.0, "apr": 6.5, "min_payment": 140.0},
]


# ---------------------------------------------------------------------------
# Cached compute — the only non-trivial computation, memoized per the brief.
# FinancialProfile is a frozen, fully-hashable dataclass, so `hash` is a valid
# hash_func; the returned SimulationResult pickles cleanly for the cache.
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, hash_funcs={engine.FinancialProfile: hash})
def run(
    profile: engine.FinancialProfile,
    income_pct: float,
    cut_pct: float,
    payoff: str | None,
) -> engine.SimulationResult:
    """Delegate to the engine. One call yields baseline + scenario metrics, deltas,
    both action plans, and the resolved/new rule sets."""
    return engine.simulate_scenario(
        profile,
        income_pct=income_pct,
        discretionary_cut_pct=cut_pct,
        payoff_debt=payoff,
    )


# ---------------------------------------------------------------------------
# Presentation helpers (formatting only)
# ---------------------------------------------------------------------------
def md_escape(s: str) -> str:
    r"""Escape '$' as '\$' so currency amounts aren't parsed as LaTeX math.

    Streamlit renders text between two '$' as a math expression (italic, spaces
    dropped). Apply to free text rendered via st.write / st.markdown / st.caption /
    st.info. Do NOT use on st.metric values — st.metric doesn't parse markdown, so a
    backslash would show literally.
    """
    return s.replace("$", "\\$")


def fmt_money(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isinf(x)):
        return "—"
    return f"${x:,.0f}"


def fmt_pct(x: float) -> str:
    """Format a fraction (0.18) as a percentage (18%)."""
    if x is None or (isinstance(x, float) and math.isinf(x)):
        return "—"
    return f"{x * 100:.0f}%"


def fmt_months(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isinf(x)):
        return "—"
    return f"{x:.1f}"


def _money_delta(x: float | bool) -> str | None:
    if isinstance(x, bool) or math.isinf(x):
        return None
    return f"{x:+,.0f}"


def _pts_delta(x: float | bool) -> str | None:
    """Delta of a fraction shown in percentage points."""
    if isinstance(x, bool) or math.isinf(x):
        return None
    return f"{x * 100:+.1f} pts"


def _months_delta(x: float | bool) -> str | None:
    if isinstance(x, bool) or math.isinf(x):
        return None
    return f"{x:+.1f} mo"


def _humanize(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _fmt_trigger_value(v: float | str | bool) -> str:
    """Render one trigger value readably (numbers, strings, booleans, infinities)."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str):
        return v
    if math.isinf(v):
        return "∞ (never, at current rate)"
    if float(v).is_integer():
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def render_card(rec: engine.Recommendation, rank: int, is_new: bool = False) -> None:
    """Render one recommendation as a bordered card with a 'show the math' expander.

    Priority is conveyed by the rank number and explicit text — never color alone.
    """
    with st.container(border=True):
        badge = "  🆕 **New in this scenario**" if is_new else ""
        # Bold markdown (not a heading) keeps the heading tree to H1 + H2s.
        st.markdown(
            f"**#{rank} · Priority {rec.priority} (1 = highest) — {rec.title}**{badge}"
        )
        st.write(md_escape(rec.projected_impact))

        with st.expander("Why this? (show the math)"):
            st.markdown(f"**Rule {rec.rule_id}** — {md_escape(rec.reason)}")
            table = pd.DataFrame(
                [
                    {"Figure": _humanize(k), "Value": _fmt_trigger_value(v)}
                    for k, v in rec.triggers.items()
                ]
            )
            st.dataframe(table, hide_index=True, use_container_width=True)
            st.caption(f"Projected impact: {md_escape(rec.projected_impact)}")


def render_plan(plan: list[engine.Recommendation], new: set[str] | None = None) -> None:
    """Render a ranked list of recommendation cards (priority 1 first)."""
    new = new or set()
    if not plan:
        st.success("No action items — your plan looks healthy. 🎉")
        return
    for rank, rec in enumerate(plan, start=1):
        render_card(rec, rank, is_new=rec.rule_id in new)


def metric_row(
    metrics: dict, deltas: dict | None = None
) -> None:
    """Render the 4 headline metrics. With `deltas`, show per-metric deltas
    (used by the simulator); without, show a plain dashboard read."""
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Monthly surplus",
        fmt_money(metrics["surplus"]),
        delta=_money_delta(deltas["surplus"]) if deltas else None,
        help="Income minus all expenses and debt minimums.",
    )
    c2.metric(
        "Savings rate",
        fmt_pct(metrics["savings_rate"]),
        delta=_pts_delta(deltas["savings_rate"]) if deltas else None,
        help="Surplus as a share of income. Target: 20%.",
    )
    c3.metric(
        "Emergency fund (months)",
        fmt_months(metrics["emergency_fund_months"]),
        delta=_months_delta(deltas["emergency_fund_months"]) if deltas else None,
        help="Months of expenses your buffer covers. Target: 3+.",
    )
    c4.metric(
        "Debt-to-income",
        fmt_pct(metrics["debt_to_income"]),
        delta=_pts_delta(deltas["debt_to_income"]) if deltas else None,
        delta_color="inverse",  # lower DTI is better
        help="Required debt payments as a share of income. Lower is better.",
    )


# ---------------------------------------------------------------------------
# Session state: seed the form + simulator knobs once (first run shows the
# sample so the dashboard looks alive immediately).
# ---------------------------------------------------------------------------
def _seed_state() -> None:
    for key, value in SAMPLE_PROFILE.items():
        st.session_state.setdefault(key, value)
    st.session_state.setdefault("debts_data", [dict(d) for d in SAMPLE_DEBTS])
    st.session_state.setdefault("debts_nonce", 0)
    st.session_state.setdefault("sim_income_pct", 0)
    st.session_state.setdefault("sim_cut_pct", 0)
    st.session_state.setdefault("sim_payoff", "(none)")


def load_sample() -> None:
    """Button callback: reset every input to the sample profile and neutral knobs."""
    for key, value in SAMPLE_PROFILE.items():
        st.session_state[key] = value
    st.session_state["debts_data"] = [dict(d) for d in SAMPLE_DEBTS]
    st.session_state["debts_nonce"] += 1  # force the data editor to re-init
    st.session_state["sim_income_pct"] = 0
    st.session_state["sim_cut_pct"] = 0
    st.session_state["sim_payoff"] = "(none)"


_seed_state()


# ---------------------------------------------------------------------------
# SECTION 1 — Sidebar onboarding form
# ---------------------------------------------------------------------------
def build_profile_from_sidebar() -> engine.FinancialProfile:
    """Collect inputs in the sidebar and hand them to the engine to validate."""
    with st.sidebar:
        st.header("Your profile")
        st.button("Load sample profile", on_click=load_sample, use_container_width=True)
        st.caption("Enter monthly amounts. APR is in percent, e.g. 24.99.")

        income = st.number_input(
            "Monthly income", min_value=0.0, step=100.0, key="income",
            help="Take-home pay you receive each month.",
        )
        fixed = st.number_input(
            "Fixed expenses", min_value=0.0, step=50.0, key="fixed",
            help="Rent, utilities, insurance — costs that don't vary much.",
        )
        variable = st.number_input(
            "Variable / discretionary expenses", min_value=0.0, step=50.0, key="variable",
            help="Dining, entertainment, shopping — the flexible spending you can cut.",
        )
        savings = st.number_input(
            "Current savings (non-emergency)", min_value=0.0, step=100.0, key="savings",
            help="Money saved toward goals, excluding your emergency fund.",
        )
        ef = st.number_input(
            "Emergency fund", min_value=0.0, step=100.0, key="ef",
            help="Cash set aside specifically for emergencies.",
        )

        st.subheader("Savings goal")
        goal_amount = st.number_input(
            "Goal amount", min_value=0.0, step=500.0, key="goal_amount",
            help="Total you want to save. Set 0 if you have no goal yet.",
        )
        goal_months = st.number_input(
            "Goal timeframe (months)", min_value=0, step=1, key="goal_months", format="%d",
            help="Months to reach the goal. Set 0 if you have no goal yet.",
        )

        st.subheader("Debts")
        st.caption("Add a row per debt. Leave empty if you have none.")
        edited = st.data_editor(
            st.session_state["debts_data"],
            num_rows="dynamic",
            use_container_width=True,
            key=f"debts_editor_{st.session_state['debts_nonce']}",
            column_config={
                "name": st.column_config.TextColumn("Debt name"),
                "balance": st.column_config.NumberColumn(
                    "Balance", min_value=0.0, step=100.0, format="$%.0f"
                ),
                "apr": st.column_config.NumberColumn(
                    "APR %", min_value=0.0, step=0.1, format="%.2f"
                ),
                "min_payment": st.column_config.NumberColumn(
                    "Min payment", min_value=0.0, step=10.0, format="$%.0f"
                ),
            },
        )

    # Shape editor rows into clean debt dicts (drop blank rows). Engine validates.
    debts: list[dict] = []
    for row in edited:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        debts.append(
            {
                "name": name,
                "balance": float(row.get("balance") or 0.0),
                "apr": float(row.get("apr") or 0.0),
                "min_payment": float(row.get("min_payment") or 0.0),
            }
        )

    try:
        return engine.FinancialProfile(
            monthly_income=income,
            fixed_expenses=fixed,
            variable_expenses=variable,
            current_savings=savings,
            emergency_fund=ef,
            debts=debts,
            goal_amount=goal_amount,
            goal_months=int(goal_months),
        )
    except ValueError as exc:
        st.error(f"Please fix your inputs: {exc}")
        st.stop()


profile = build_profile_from_sidebar()

# Keep the "pay off a debt" choice valid if the underlying debts changed.
payoff_options = ["(none)"] + [d.name for d in profile.debts]
if st.session_state["sim_payoff"] not in payoff_options:
    st.session_state["sim_payoff"] = "(none)"

# Single engine call drives baseline (sections 2-3) and scenario (section 4).
payoff_arg = None if st.session_state["sim_payoff"] == "(none)" else st.session_state["sim_payoff"]
result = run(
    profile,
    st.session_state["sim_income_pct"],
    st.session_state["sim_cut_pct"],
    payoff_arg,
)


# ---------------------------------------------------------------------------
# Header (one H1) + disclaimer
# ---------------------------------------------------------------------------
st.title("🧭 FinPilot")
st.markdown("A transparent, glass-box coach — every recommendation shows its math.")
st.caption(DISCLAIMER)


# ---------------------------------------------------------------------------
# SECTION 2 — Dashboard (saved profile only, no what-if)
# ---------------------------------------------------------------------------
st.header("Your financial snapshot")
metric_row(result.baseline_metrics)  # baseline numbers, no deltas

# Outflow breakdown — rendered WITHOUT altair. st.bar_chart imports altair, whose
# generated schema (TypedDict(..., closed=True)) crashes on Streamlit Cloud's Python
# 3.14. st.dataframe + ProgressColumn gives a labeled horizontal-bar breakdown and
# never touches altair, so the render path stays 3.14-safe.
breakdown = {
    "Fixed": profile.fixed_expenses,
    "Variable": profile.variable_expenses,
}
for d in profile.debts:
    breakdown[f"{d.name} (min)"] = d.min_payment

max_amount = max(breakdown.values())  # breakdown always has Fixed + Variable
outflow_df = pd.DataFrame(
    {"Category": list(breakdown.keys()), "Monthly $": list(breakdown.values())}
)
st.dataframe(
    outflow_df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Category": st.column_config.TextColumn("Category"),
        "Monthly $": st.column_config.ProgressColumn(
            "Monthly outflow",
            format="$%.0f",
            min_value=0,
            max_value=float(max_amount) if max_amount > 0 else 1.0,
        ),
    },
)
st.caption(
    md_escape(
        "Monthly outflow by category: "
        + ", ".join(f"{k} {fmt_money(v)}" for k, v in breakdown.items())
        + f". Total monthly outflow {fmt_money(engine.monthly_expenses(profile))}."
    )
)


# ---------------------------------------------------------------------------
# SECTION 3 — The action plan (headline feature; baseline profile)
# ---------------------------------------------------------------------------
st.header("Your action plan")
st.markdown("Ranked by priority (1 = most urgent). Open **Why this?** for the math behind each.")
render_plan(result.baseline_plan)

# Optional natural-language narration (works with or without an AI key).
if st.button("Explain my plan in plain English"):
    with st.spinner("Writing your summary..."):
        st.session_state["narration"] = llm.narrate(result.baseline_plan, profile)
if st.session_state.get("narration"):
    # Narration is built from the same reason/projected_impact strings, so escape it too.
    st.info(md_escape(st.session_state["narration"]))
    source = "AI-generated (Gemini)" if llm.ai_enabled() else "Rule-based summary"
    st.caption(f"{source} · {DISCLAIMER}")


# ---------------------------------------------------------------------------
# SECTION 4 — Scenario simulator (the only what-if section)
# ---------------------------------------------------------------------------
st.header("Scenario simulator")
st.markdown("Drag the controls to test a what-if. The sections above stay on your saved profile.")

c1, c2 = st.columns(2)
with c1:
    st.slider(
        "Adjust income (%)", min_value=-50, max_value=50, key="sim_income_pct",
        help="Simulate a raise (+) or an income drop (−).",
    )
with c2:
    st.slider(
        "Cut discretionary spending (%)", min_value=0, max_value=100, key="sim_cut_pct",
        help="Trim variable expenses by this percentage.",
    )
st.selectbox(
    "Pay off a debt", options=payoff_options, key="sim_payoff",
    disabled=len(profile.debts) == 0,
    help="Remove a debt entirely to see the effect of paying it off.",
)

# Simulated outcome: metrics WITH deltas vs the saved profile.
st.markdown("**Simulated outcome** (deltas vs your saved profile)")
metric_row(result.scenario_metrics, result.deltas)

# What changed: which rules cleared or appeared (titles pulled from the engine plans).
baseline_titles = {r.rule_id: r.title for r in result.baseline_plan}
scenario_titles = {r.rule_id: r.title for r in result.scenario_plan}
if result.resolved_rules:
    st.success(
        "✅ Resolves: "
        + ", ".join(f"{rid} — {baseline_titles[rid]}" for rid in result.resolved_rules)
    )
if result.new_rules:
    st.warning(
        "⚠ New issues: "
        + ", ".join(f"{rid} — {scenario_titles[rid]}" for rid in result.new_rules)
    )
if not result.resolved_rules and not result.new_rules:
    st.info("Same rules fire as your saved plan — adjust the controls to see changes.")

render_plan(result.scenario_plan, new=set(result.new_rules))

# Footer
st.divider()
st.caption(DISCLAIMER)
