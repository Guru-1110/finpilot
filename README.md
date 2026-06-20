# FinPilot

A transparent **"glass-box"** personal-finance decision coach. Instead of a
black-box score, FinPilot explains every recommendation: the rule that fired, the
exact numbers that triggered it, and the projected impact of acting on it. You can
audit *why* it's telling you something — not just *what*.

This repo currently contains the **core decision engine only** (pure Python, fully
tested). A Streamlit UI will be layered on top in a later phase.

## Rules

| ID | Fires when | Action |
|----|------------|--------|
| R1 | Emergency fund covers < 3 months of expenses | Build a 3-month buffer |
| R2 | Any debt APR > 15% | Avalanche the highest-APR debt first |
| R3 | Spending exceeds income (negative cash flow) | Cut the largest discretionary category |
| R4 | Savings rate < 20% | Lift savings to the 20% target |
| R5 | Savings goal not reachable at current surplus | Close the monthly gap |

`generate_action_plan(profile)` returns the fired recommendations sorted by
priority (1 = most urgent). `simulate(profile, **overrides)` runs "what-if"
scenarios (income change, expense cut, debt payoff) and reports the metric deltas
plus which rules were resolved or newly triggered.

## Layout

```
finpilot/
├── engine.py            # all decision logic; no Streamlit import
├── conftest.py          # empty — lets tests `import engine`
├── tests/
│   └── test_engine.py   # pytest suite
├── requirements.txt
├── .gitignore
└── README.md
```

## Getting started

```bash
pip install -r requirements.txt
python -m pytest          # run the test suite
python -c "import engine" # sanity: engine has no third-party imports
```

> APR is entered in **percent points** (e.g. `18.5` means 18.5%).

## Roadmap

- [ ] Streamlit UI for entering a profile and exploring the action plan
- [ ] Interactive what-if sliders backed by `simulate()`
