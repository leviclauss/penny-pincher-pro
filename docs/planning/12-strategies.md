# 12 — Screener Strategies

A "screener strategy" is a named **filter config** — a JSON object listing
filters, parameters, and per-filter scoring weights. The pipeline applies
each config to every active ticker once a day; configs are independent
and rank their own passers.

The catalog of available filters is in
[`docs/planning/02-screener-filters.md`](02-screener-filters.md); the
runtime registry is
[`backend/screener/registry.py`](../../backend/screener/registry.py).
This doc covers the **strategies** — the named, opinionated combinations
of those filters that ship as defaults.

## Defaults at a glance

Six configs ship via
[`backend/scripts/seed_filter_configs.py`](../../backend/scripts/seed_filter_configs.py)
(run `python -m scripts.seed_filter_configs`, idempotent). Each
represents a different opinion on what "wheel-worthy" means.

| Strategy | Thesis | Best regime |
|---|---|---|
| Conservative Wheel — 200EMA Touch | Quality names pulling back to long-term support | High-IV pullback in an uptrend |
| Premium Hunter — High IV Rank | Aggressive premium capture on uptrends with elevated IV | Elevated VIX, vol-rich names |
| Bollinger Bottom Reversal | Mean-reversion entry: lower-band touch with rich IV | Range-bound or choppy markets |
| Blue Chip Income | Tier-1 mega-caps only, tight concentration | Anytime — lowest variance baseline |
| Trend Pullback — 50EMA Bounce | Bullish continuation: established uptrend pulling back to the 50 EMA | Strong uptrend, moderate IV |
| Volatility Spike Hunter | Extreme IV regime — capitalize on vol mean-reversion | Post-shock, IV rank ≥ 75 |

---

## Conservative Wheel — 200EMA Touch

High-IV pullbacks to long-term support on quality names. The default
config and the one demonstrated in
[`docs/planning/02-screener-filters.md`](02-screener-filters.md).

**Required filters** (every named criterion is hard-gated — a config
named "200EMA Touch" doesn't fire without an actual 200EMA touch)

- `weekly_above_200ema` — regime gate; only buy puts on names in a
  weekly uptrend.
- `near_200ema` (within 3%) — buy support, not the rip.
- `rsi_oversold` (≤ 40) — confirm the pullback is real.
- `iv_percentile_high` (≥ 50) — only sell when premium is rich.
- `no_earnings_in_window` (45 days) — earnings are uncorrelated risk
  the screener can't price.
- `min_market_cap` (≥ $10B) — liquidity floor.
- `tier_allowed` ([1, 2]).
- `not_freefall` (5d return ≥ -10%) — don't catch knives.

**Optional filters** (scoring contributors only)

- `iv_rank_high` (≥ 40) — bonus signal; contributes to the score but
  doesn't gate.
- `sector_concentration` (max 3) — post-processor.

**Scoring weights:** `iv_percentile_high` 0.35, `near_200ema` 0.25,
`rsi_oversold` 0.25, `iv_rank_high` 0.15.

---

## Premium Hunter — High IV Rank

Aggressive premium capture on uptrending names with elevated IV. Looser
EMA proximity than the conservative config; harder IV-rank floor.

**Required filters**

- `weekly_above_200ema`.
- `iv_rank_high` (≥ 70) — the whole point.
- `iv_above_hv` (≥ 1.05) — premium has to be priced rich relative to
  realized vol.
- `near_50ema` (within 5%) — entry has to be on a pullback, not the
  blow-off top.
- `no_earnings_in_window` (35 days).
- `min_market_cap` (≥ $5B).
- `tier_allowed` ([1, 2]).
- `not_freefall` (≥ -12%).

**Optional filters** (scoring contributors only)

- `rsi_oversold` (≤ 55) — broader band; we're not waiting for deep pullbacks.
- `iv_percentile_high` (≥ 60).
- `sector_concentration` (max 4).

**Scoring weights:** `iv_rank_high` 0.40, `iv_percentile_high` 0.25,
`near_50ema` 0.20, `rsi_oversold` 0.15.

---

## Bollinger Bottom Reversal

Mean-reversion entry: lower-band touch on quality names with
premium-rich IV and no near-term earnings. No `weekly_above_200ema`
gate — this strategy explicitly looks for snap-backs in weaker tape.

**Required filters**

- `bb_lower_touch`.
- `rsi_oversold` (≤ 35).
- `iv_percentile_high` (≥ 40) — premium floor; reversal trades only
  pay if vol is rich.
- `not_freefall` (≥ -15%).
- `no_earnings_in_window` (30 days).
- `min_market_cap` (≥ $2B) — lowest cap floor of any default.
- `tier_allowed` ([1, 2, 3]).

**Optional filters** (scoring contributors only)

- `iv_rank_high` (≥ 40).
- `iv_above_hv` (≥ 1.0).
- `sector_concentration` (max 3).

**Scoring weights:** `rsi_oversold` 0.40, `iv_percentile_high` 0.30,
`iv_rank_high` 0.30.

---

## Blue Chip Income

Tier-1 mega-caps only — modest premium expectations, tight concentration,
long earnings buffer. Lowest-variance default.

**Required filters**

- `weekly_above_200ema`.
- `tier_allowed` ([1] — tier 1 only).
- `min_market_cap` (≥ $50B — highest floor).
- `no_earnings_in_window` (45 days).
- `iv_percentile_high` (≥ 40) — "income" means real premium, not a
  flatlined name.
- `not_freefall` (≥ -8%) — tightest knife-catching guard.

**Optional filters** (scoring contributors only)

- `near_200ema` (within 5%) — bonus signal when present, but income
  strategies don't strictly require a touch.
- `rsi_oversold` (≤ 50).
- `sector_concentration` (max 2).

**Scoring weights:** `iv_percentile_high` 0.40, `near_200ema` 0.30,
`rsi_oversold` 0.30.

---

## Trend Pullback — 50EMA Bounce

Bullish-continuation setup: established uptrend pulling back to the 50
EMA with moderate IV and no earnings.

**Required filters**

- `weekly_above_200ema`.
- `near_50ema` (within 2.5%) — the trigger.
- `rsi_oversold` (≤ 55) — "pullback" is in the name; needs a real dip.
- `iv_percentile_high` (≥ 35) — premium floor.
- `no_earnings_in_window` (35 days).
- `min_market_cap` (≥ $10B).
- `tier_allowed` ([1, 2]).
- `not_freefall` (≥ -10%).

**Optional filters** (scoring contributors only)

- `iv_rank_high` (≥ 35).
- `sector_concentration` (max 3).

**Scoring weights:** `near_50ema` 0.40, `iv_percentile_high` 0.20,
`iv_rank_high` 0.20, `rsi_oversold` 0.20.

---

## Volatility Spike Hunter

Extreme IV regime: rank ≥ 75 and IV/HV stretch — best for short-dated
CSPs that capitalize on vol mean-reversion.

**Required filters**

- `iv_rank_high` (≥ 75).
- `iv_above_hv` (≥ 1.15).
- `iv_percentile_high` (≥ 65) — three IV gates because the whole
  thesis is vol mean-reversion.
- `min_market_cap` (≥ $10B).
- `no_earnings_in_window` (21 days — shortest window since this strategy targets short DTEs).
- `tier_allowed` ([1, 2]).
- `not_freefall` (≥ -15%).

**Optional filters** (scoring contributors only)

- `rsi_oversold` (≤ 50).
- `sector_concentration` (max 3).

**Scoring weights:** `iv_rank_high` 0.45, `iv_percentile_high` 0.30,
`rsi_oversold` 0.25.

---

## Adding a new strategy

Configs are first-class data — they live as rows in `filter_configs`
keyed by name. To add a new default:

1. Define the config dict in `seed_filter_configs.py` and append it to
   `ALL_CONFIGS`. The seeder is idempotent and matches by name.
2. Re-run `python -m scripts.seed_filter_configs`. To refresh an
   already-seeded config after tweaking its defaults, run with
   `--update-existing`.

To create a one-off (non-default) config, `POST /api/configs` with the
same JSON shape, or use the `/configs` page once it ships.

For adding a brand-new **filter** (not just a new combination of
existing filters), see "How to add a screener filter" in `CLAUDE.md` and
the filter catalog in
[`docs/planning/02-screener-filters.md`](02-screener-filters.md).
