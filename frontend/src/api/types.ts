export interface HealthStatus {
  status: string;
  app_env: string;
  server_time_utc: string;
  database_url_scheme: string;
  last_bar_date: string | null;
  bar_count: number;
}

export interface TickerSummary {
  symbol: string;
  name: string | null;
  tier: number | null;
  sector: string | null;
  market_cap: number | null;
  is_active: boolean;
  is_hidden: boolean;
  last_close: number | null;
  last_close_date: string | null;
  ema_200: number | null;
  rsi_14: number | null;
  iv_atm: number | null;
  next_earnings_date: string | null;
}

export interface TickerCreate {
  symbol: string;
  name?: string | null;
  tier?: number | null;
  notes?: string | null;
}

export interface TickerPatch {
  is_hidden?: boolean;
  tier?: number | null;
  notes?: string | null;
}

export interface JobRunOut {
  id: number;
  job_name: string;
  status: "running" | "success" | "failure";
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
  result_json: Record<string, unknown> | null;
  error: string | null;
}

export interface JobInfoOut {
  name: string;
  description: string;
  schedule: string;
  cron: string;
  timezone: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run: JobRunOut | null;
}

export interface TriggerResponse {
  job_name: string;
  accepted: boolean;
  detail: string;
}

export interface ChartBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ema_20: number | null;
  ema_50: number | null;
  ema_200: number | null;
  rsi_14: number | null;
}

export interface IVPoint {
  date: string;
  iv_atm: number | null;
  iv_rank: number | null;
  iv_percentile: number | null;
}

export interface MacroPoint {
  date: string;
  vix_close: number | null;
  vix_9d: number | null;
  vix_term_structure: number | null;
  spy_close: number | null;
  spy_ema_200: number | null;
  spy_above_200ema: boolean | null;
}

export interface AlertPreference {
  alert_type: string;
  channels: string[];
  enabled: boolean;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
}

export interface AlertPreferenceUpdate {
  channels: string[];
  enabled: boolean;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
}

export interface ChannelsStatus {
  telegram: boolean;
}

export interface UpcomingEarning {
  symbol: string;
  name: string | null;
  earnings_date: string;
  time_of_day: string | null;
}

export interface ScreenerConfigSummary {
  id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  filter_ids: string[];
  updated_at: string;
}

export interface ScreenerConfigDetail extends ScreenerConfigSummary {
  config_json: Record<string, unknown>;
}

export type FilterParamKind = "number" | "integer" | "percent" | "currency" | "tier_set";

export interface FilterParamSchema {
  name: string;
  label: string;
  kind: FilterParamKind;
  default: number | number[];
  min: number | null;
  max: number | null;
  step: number | null;
  description: string | null;
}

export type FilterCategory = "trend" | "volatility" | "liquidity" | "event";

export interface FilterCatalogEntry {
  id: string;
  label: string;
  description: string;
  category: FilterCategory;
  scored: boolean;
  params: FilterParamSchema[];
}

export interface ScreenerFilterEntryIn {
  id: string;
  params?: Record<string, unknown>;
  required?: boolean;
}

export interface ScreenerConfigWriteIn {
  name: string;
  description?: string | null;
  is_active?: boolean;
  filters: ScreenerFilterEntryIn[];
  scoring?: { weights?: Record<string, number> };
}

export interface ScreenerFilterEntry {
  passed: boolean;
  eligible: boolean;
  required: boolean;
  score: number | null;
  value: number | string | null;
  reason: string | null;
}

export interface ScreenerResultRow {
  date: string;
  symbol: string;
  config_id: number;
  passed: boolean;
  score: number | null;
  sector: string | null;
  rsi_14: number | null;
  iv_rank: number | null;
  iv_percentile: number | null;
  near_200ema_pct: number | null;
  next_earnings_date: string | null;
  filter_results: Record<string, ScreenerFilterEntry> | null;
}

export interface ScreenerResultsResponse {
  date: string;
  config_id: number;
  config_name: string;
  rows: ScreenerResultRow[];
}

export type PositionState = "short_put" | "long_shares" | "covered_call" | "closed";

export interface PositionLegOut {
  id: number;
  leg_type: string;
  symbol: string;
  expiration: string | null;
  strike: number | null;
  contracts: number | null;
  shares: number | null;
  entry_price: number | null;
  exit_price: number | null;
  entry_date: string | null;
  exit_date: string | null;
  outcome: string | null;
  realized_pnl: number | null;
  fees: number;
}

export interface PositionSnapshotOut {
  snapshot_at: string;
  underlying_price: number | null;
  option_mid: number | null;
  unrealized_pnl: number | null;
  pct_max_profit: number | null;
  delta: number | null;
  dte: number | null;
}

export type AcquisitionSource = "open_market" | "assignment";

export interface PositionOut {
  id: number;
  symbol: string;
  state: PositionState | string;
  cycle_id: number | null;
  opened_at: string;
  closed_at: string | null;
  notes: string | null;
  acquisition_source: AcquisitionSource | null;
  legs: PositionLegOut[];
  latest_snapshot: PositionSnapshotOut | null;
}

export interface PositionAttributionOut {
  position_id: number;
  symbol: string;
  days_in_cycle: number | null;
  total_premium_collected: number;
  shares_pnl: number;
  realized_pnl: number;
  cost_basis_per_share: number | null;
  capital_tied_up: number | null;
  annualized_return: number | null;
  was_assigned: boolean;
}

export interface OpenShortPutInput {
  symbol: string;
  expiration: string;
  strike: number;
  contracts: number;
  credit: number;
  opened_on: string;
  fees?: number;
  notes?: string | null;
}

export interface OpenCoveredCallInput {
  expiration: string;
  strike: number;
  contracts: number;
  credit: number;
  opened_on: string;
  fees?: number;
}

export interface OpenLongSharesInput {
  symbol: string;
  shares: number;
  cost_basis: number;
  opened_on: string;
  acquisition_source: AcquisitionSource;
  fees?: number;
  notes?: string | null;
}

export interface OpenCoveredCallFreshInput extends OpenLongSharesInput {
  expiration: string;
  strike: number;
  contracts: number;
  credit: number;
}

export interface CloseDebitInput {
  debit: number;
  closed_on: string;
  fees?: number;
}

export interface ExpireInput {
  expired_on: string;
}

export interface AssignInput {
  assigned_on: string;
}

export interface CalledAwayInput {
  called_on: string;
}

export interface CloseSharesInput {
  sale_price: number;
  closed_on: string;
  fees?: number;
}
