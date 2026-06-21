# FinPilot 🧭

A transparent, **glass-box** personal-finance decision coach. Instead of a black-box score,
FinPilot shows the reasoning behind every recommendation: the rule that fired, the exact
numbers that triggered it, and the projected impact of acting on it. You can audit *why* it's
telling you something — not just *what*.

> **FinPilot is an educational tool, not licensed financial advice.** All figures are
> estimates to help you reason about your money.

---

## Chosen vertical: Personal Finance

FinPilot helps an individual triage their monthly finances. From a simple profile (income,
expenses, savings, debts, and one savings goal) it produces a **ranked action plan** — what to
fix first and why — and lets the user test "what-if" scenarios before making a decision.

## Approach & logic

The whole system is split into a **pure decision engine** and a **thin presentation layer**, so
the financial logic is testable in isolation and never entangled with the UI.

**1. Inputs → metrics.** `engine.py` turns a `FinancialProfile` into headline metrics, each a
small pure function with explicit divide-by-zero guards:

| Metric | Definition |
|--------|------------|
| Monthly surplus | `income − fixed − variable − debt minimums` |
| Savings rate | `surplus / income` (0 when income is 0) |
| Emergency-fund months | `emergency_fund / monthly expenses` |
| Debt-to-income | `debt minimums / income` |
| Goal feasibility | required vs. actual monthly, shortfall, reachability |

**2. The rules engine.** Five rules each inspect the metrics and, when they fire, return a
`Recommendation` carrying its full reasoning. The plan is sorted by priority (1 = most urgent):

| ID | Fires when | Priority | Action |
|----|------------|----------|--------|
| R3 | Spending exceeds income (negative cash flow) | 1 | Cut the largest discretionary category |
| R2 | Any debt APR > 15% | 2 | Avalanche the highest-APR debt first (interest saved vs. minimums) |
| R1 | Emergency fund covers < 3 months | 3 | Build a 3-month buffer (months-to-reach at current surplus) |
| R4 | Savings rate < 20% | 4 | Lift savings to the 20% target (dollar gap) |
| R5 | Savings goal not reachable at current surplus | 5 | Close the monthly gap |

**3. The glass-box reasoning panel.** Every `Recommendation` stores a `rule_id`, a human-readable
`reason`, a `triggers` dict (the exact numbers that fired it), and a `projected_impact`. The UI
renders each as a ranked card with a **"Why this? (show the math)"** expander that lays the
reasoning bare. The R2 "interest saved" figure comes from a real amortization comparison
(minimum-only vs. minimum + surplus payoff), not a hand-wave.

**4. What-if simulation.** `simulate()` / `simulate_scenario()` build a new profile via
`dataclasses.replace` (immutably) and report per-metric **deltas** plus which rules were
**resolved** or **newly triggered** — powering the scenario sliders.

## How it works

### Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL Streamlit prints (default http://localhost:8501).

### How the UI behaves

- **Sidebar — Your profile.** Enter income, expenses, savings, emergency fund, a savings goal,
  and a dynamic table of debts (APR in percent, e.g. `24.99`). A **"Load sample profile"** button
  fills a realistic demo in one click. Inputs persist across reruns.
- **Your financial snapshot.** The four headline metrics for your saved profile, plus a bar chart
  of your monthly outflow (with a text caption describing every value).
- **Your action plan.** The ranked recommendation cards — the headline feature — each with its
  "Why this? (show the math)" panel. An **"Explain my plan in plain English"** button produces a
  friendly narration (see below).
- **Scenario simulator.** Sliders for income ±%, cutting discretionary spending %, and a
  "pay off a debt" selector. This is the only section that shows what-if results: the metrics
  update with deltas, and the plan re-renders showing what your change resolves or introduces.

### Optional AI narration

The **"Explain my plan in plain English"** button calls `llm.narrate()`:

- **With an API key** it calls Google **Gemini** (`gemini-1.5-flash`) with a strict system prompt
  — *"Explain ONLY the provided plan… Do not invent numbers or advice"* — passing the structured
  plan as the only allowed facts. (Gemini was chosen because it offers a free tier, which suits a
  bootstrapped build.)
- **With no key** it returns a deterministic plain-English summary built directly from the plan.

The app works **perfectly with no key**. To enable AI narration, copy
`.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` and add your key, or set the
`GEMINI_API_KEY` (or `GOOGLE_API_KEY`) environment variable. The key is **never hardcoded** and
the real `secrets.toml` is gitignored. Get a free key at
<https://aistudio.google.com/app/apikey>.

## Assumptions

- **Thresholds** (named constants in `engine.py`): 3-month emergency-fund minimum, 15% high-APR
  line, 20% target savings rate.
- **APR** is entered in **percent points** (`18.5` = 18.5%).
- **Monthly surplus** subtracts required debt minimums explicitly, so cash flow is honest rather
  than assuming minimums are baked into fixed expenses.
- **Current savings** is credited toward your goal; the emergency fund is tracked separately.
- The "interest saved" and "months to reach" figures are **estimates** for reasoning, not exact
  amortization schedules.
- **All figures are educational estimates, not financial advice.**

## Testing

The decision engine and narration fallback are unit-tested with **pytest**:

```bash
python -m pytest                 # run the full suite
python -m pytest --cov=engine --cov=llm --cov-report=term-missing   # with coverage
```

Tests cover every rule **firing and not firing**, boundary values (exactly 3-month fund, 15.0%
APR, 20% savings rate, zero income, no debts, unreachable goal), the what-if simulator, and the
offline narration fallback (no key, no network).

## Accessibility

- **One H1, then H2 sections** in logical reading order.
- **Every input has a visible label** plus `help` tooltip text.
- The outflow **chart has a text caption** spelling out each value (not chart-only information).
- **Priority is conveyed by rank number + text** ("#1 · Priority 1"), never by color alone.
- **High-contrast light theme** (`.streamlit/config.toml`): near-black text on white, AA contrast,
  readable on projectors.

## Security

- **No hardcoded secrets.** API keys are read only from `st.secrets` or environment variables.
- `.env` and `.streamlit/secrets.toml` are **gitignored**; only a placeholder `.example` is
  committed.
- **All inputs validated** in the engine (`FinancialProfile.__post_init__` rejects negatives and
  bad types); the UI surfaces validation errors instead of crashing.
- **No `eval` / `exec` / `subprocess`.**
- **Pinned dependencies** in `requirements.txt`.

## Layout

```
finpilot/
├── engine.py                     # pure decision logic; no Streamlit import
├── app.py                        # Streamlit UI; zero business logic
├── llm.py                        # optional AI narration + deterministic fallback
├── conftest.py                   # empty — lets tests `import engine`
├── tests/
│   ├── test_engine.py            # engine unit tests
│   └── test_llm.py               # offline narration tests
├── .streamlit/
│   ├── config.toml               # high-contrast light theme
│   └── secrets.toml.example      # key template (real secrets.toml is gitignored)
├── requirements.txt
├── .gitignore
└── README.md
```
