# 11 — Screener Config UI

Tuning a filter config today means editing
`backend/scripts/seed_filter_configs.py` and re-running it. This plan
moves config CRUD into the web UI: list / create / edit / duplicate /
deactivate, with per-filter parameter forms generated from a
machine-readable schema.

The work is split into five PRs so each one is reviewable in isolation
and the backend lands ahead of the UI that consumes it.

## Prior art in this repo

- Read-only API for configs lives at `backend/api/screener.py`
  (`GET /configs`, `GET /configs/{id}`, `GET /results`).
- Filter classes live under `backend/screener/filters/` (one module per
  category). Param keys are currently implicit in each filter's
  `evaluate()` body.
- `FILTER_REGISTRY` in `backend/screener/registry.py` maps the JSON
  `filters[].id` strings to filter classes.
- Default config is seeded by `backend/scripts/seed_filter_configs.py`
  and matches the example in
  [02-screener-filters.md](02-screener-filters.md) §"Filter config
  example".
- The Screener page lives at `frontend/src/pages/Screener.tsx` and lets
  the user pick an existing config from a dropdown — there's no edit
  affordance.

## Config JSON shape (frozen contract)

The shape stored in `filter_configs.config_json` and accepted by future
write endpoints is the one already used by the seed script:

```json
{
  "name": "Conservative Wheel - 200EMA Touch",
  "description": "High-IV pullbacks to long-term support on quality names",
  "filters": [
    {"id": "near_200ema", "params": {"max_pct": 0.03}, "required": false}
  ],
  "scoring": {
    "weights": { "iv_percentile_high": 0.35 }
  }
}
```

`required` defaults to `false`. `params` is optional (filters fall back
to the defaults declared in their `PARAM_SCHEMA`). `scoring.weights`
keys must reference filter IDs present in `filters[]` — write
endpoints validate this.

## PR breakdown

### PR 1 — Filter param schema + catalog endpoint *(this PR)*

Goal: expose the registry as machine-readable metadata so a UI can
render forms without hardcoding filter IDs.

- [x] Add a `ParamSpec` dataclass to `screener/filters/base.py`:

  ```python
  ParamKind = Literal["number", "integer", "percent", "currency", "tier_set"]

  @dataclass(frozen=True, slots=True)
  class ParamSpec:
      name: str
      label: str
      kind: ParamKind
      default: float | int | tuple[int, ...]
      min: float | None = None
      max: float | None = None
      step: float | None = None
      description: str | None = None
  ```

  Kinds map to UI controls: `percent` is a fractional float (0.03 = 3%),
  `currency` is USD, `tier_set` is a multi-select chip group over `[1,
  2, 3]`.

- [x] Extend the `Filter` Protocol with class-level metadata:
  `label`, `description`, `category` (`"trend" | "volatility" |
  "liquidity" | "event"`), `param_schema`, and `scored` (whether the
  filter contributes a score and is therefore weightable).

- [x] Populate the metadata on every filter class in
  `screener/filters/{technical,volatility,liquidity,event}.py`. Defaults
  in `PARAM_SCHEMA` are the source of truth — the literal constants at
  the top of each module (e.g. `NEAR_200EMA_DEFAULT_MAX_PCT`) get reused
  to stay in sync.

- [x] Add `GET /api/screener/filters` returning the catalog:

  ```json
  [
    {
      "id": "near_200ema",
      "label": "Near 200 EMA",
      "description": "Close within max_pct of the 200-day EMA.",
      "category": "trend",
      "scored": true,
      "params": [
        {
          "name": "max_pct",
          "label": "Max distance",
          "kind": "percent",
          "default": 0.03,
          "min": 0.0, "max": 0.5, "step": 0.005,
          "description": null
        }
      ]
    }
  ]
  ```

- [x] Tests:
  - `test_filter_registry.py` asserts every registered class declares
    non-empty `label`, `description`, `category`, and `param_schema`
    (or explicit empty for paramless filters).
  - `test_screener_api.py` asserts the catalog endpoint returns one
    entry per registry ID and round-trips the seeded defaults.

Not in scope for PR 1: any write endpoints, any frontend changes.

### PR 2 — Config write endpoints *(this PR)*

- [x] `POST /api/screener/configs` — create. Validates filter IDs,
  param presence/types against `PARAM_SCHEMA`, and that
  `scoring.weights` only references scored filters present in `filters[]`.
- [x] `PUT /api/screener/configs/{id}` — full replace; `updated_at`
  is bumped automatically by the model's `onupdate=utcnow`.
- [x] `DELETE /api/screener/configs/{id}` — 409 if `screener_results`
  rows reference it; suggest deactivation instead. `?cascade=true`
  forces hard delete (admin escape hatch).
- [x] `PATCH /api/screener/configs/{id}/active` — toggle `is_active`
  without round-tripping the whole JSON.

`sector_concentration` is allowed inside `filters[]` as a special-case
postprocessor (the pipeline already understands it) but is rejected
from `scoring.weights` because it's not a scored filter. The full
postprocessor catalog is still parked under "Open questions".

Tests cover the happy path plus rejection of unknown filter IDs,
unknown params, out-of-range param values, duplicate filter IDs,
weights referencing absent or unscored filters, and the 409 on
delete-with-results.

### PR 3 — Frontend `/screener/configs` list page *(this PR)*

- [x] New `pages/ScreenerConfigs.tsx` with a TanStack Query-backed table:
  name, description, # filters, active toggle, last updated, row
  actions (Edit, Duplicate, Delete).
- [x] Active toggle uses the PATCH endpoint with optimistic updates and
  rollback on error.
- [x] Delete is a confirm dialog; on 409 it offers "Deactivate instead?".
- [x] Sidebar link added; gear icon next to the dropdown on the existing
  Screener page links here.

The Edit pencil links to `/screener/configs/{id}` which 404s until PR 4
ships the editor. Duplicate fetches the source detail, suffixes the
name with " (copy)", flips `is_active=false`, and POSTs. The
`FilterConfigSummary` API response gained an `updated_at` field so the
list can show "last updated" without an extra detail roundtrip.

### PR 4 — Frontend config editor

- New `pages/ScreenerConfigEditor.tsx` reachable at
  `/screener/configs/new` and `/screener/configs/:id`.
- Three sections: identity (name, description, active), filters
  (sortable list of cards with per-param inputs typed from
  `PARAM_SCHEMA` + an "add filter" picker grouped by category), and
  scoring (weight inputs only for filters with `scored=true`, with a
  live "weights sum" indicator).
- Sticky footer with Cancel / Save (disabled until dirty + valid) +
  overflow menu (Duplicate, View as JSON).
- Client-side validation mirrors what the POST endpoint enforces.

### PR 5 — Wire affordances into Screener page

- "+ New config" item at the bottom of the dropdown on
  `frontend/src/pages/Screener.tsx`.
- "Edit config" pencil button next to the dropdown that links to
  `/screener/configs/{configId}`.
- After mutations, `queryClient.invalidateQueries(["screener",
  "configs"])` so the dropdown picks up new/renamed configs without a
  page reload.

## Open questions parked for later

- **Postprocessors**: `sector_concentration` is a cross-symbol rule
  that doesn't fit the per-ticker `Filter` protocol. PR 1 omits it
  from the catalog. When the postprocessor registry lands, the editor
  grows a third section ("Cross-symbol rules"), the catalog endpoint
  gains a sibling at `/api/screener/postprocessors`, and the seed
  config's reference to `sector_concentration` becomes valid again.
- **Soft vs hard delete**: PR 2 prefers soft delete (deactivate) and
  only allows hard delete via `?cascade=true`. If we ever need
  per-config history retention guarantees this becomes the place to
  add a `deleted_at` column instead.
