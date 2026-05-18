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
  ticker_source: "watchlist" | "universe";
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
  email: boolean;
  ntfy: boolean;
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

export type FilterParamKind =
  | "number"
  | "integer"
  | "percent"
  | "currency"
  | "tier_set"
  | "sector_set";

export interface FilterParamSchema {
  name: string;
  label: string;
  kind: FilterParamKind;
  default: number | number[] | string[];
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
  ticker_source: string;
  rsi_14: number | null;
  iv_rank: number | null;
  iv_percentile: number | null;
  near_200ema_pct: number | null;
  next_earnings_date: string | null;
  target_strike: number | null;
  target_expiration: string | null;
  target_premium: number | null;
  target_delta: number | null;
  annualized_return: number | null;
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
  portfolio_id: number | null;
  legs: PositionLegOut[];
  latest_snapshot: PositionSnapshotOut | null;
}

export interface PortfolioOut {
  id: number;
  name: string;
  created_at: string;
  position_count: number;
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
  portfolio_id?: number | null;
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
  portfolio_id?: number | null;
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

export interface AlertOut {
  id: number;
  alert_type: string;
  symbol: string | null;
  config_id: number | null;
  payload: Record<string, unknown>;
  triggered_at: string;
  channels_sent: string[];
  user_acked: boolean;
}

export interface AlertListParams {
  since?: string | null;
  until?: string | null;
  alertType?: string | null;
  symbol?: string | null;
  limit?: number;
  offset?: number;
}

export type BacktestMode = "filter" | "strategy";
export type BacktestStatus = "running" | "completed" | "failed";

export interface BacktestRunOut {
  id: number;
  config_id: number | null;
  config_name: string | null;
  mode: BacktestMode;
  status: BacktestStatus;
  error_message: string | null;
  start_date: string;
  end_date: string;
  starting_capital: number;
  params_json: Record<string, unknown> | null;
  created_at: string;
  trade_count: number;
  // Filter-mode metrics (null on strategy runs)
  win_rate: number | null;
  mean_return_pct: number | null;
  median_return_pct: number | null;
  // Strategy-mode metrics (null on filter runs)
  final_equity: number | null;
  total_return_pct: number | null;
  cycles_completed: number | null;
  // Full metric pack (Sharpe, drawdown, win-rate, etc.) on strategy runs.
  metrics: BacktestMetrics | null;
}

export interface BacktestMetrics {
  sharpe: number | null;
  sortino: number | null;
  max_drawdown_pct: number | null;
  cagr: number | null;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  cycles_completed: number;
  assignment_rate: number | null;
  avg_dte_held: number | null;
}

export interface BacktestTradeOut {
  id: number;
  symbol: string;
  cycle_id: number | null;
  leg_type: string;
  entry_date: string;
  exit_date: string | null;
  strike: number | null;
  expiration: string | null;
  entry_price: number;
  exit_price: number | null;
  outcome: string | null;
  realized_pnl: number | null;
  realized_pnl_pct: number | null;
  fees: number;
  meta: Record<string, unknown> | null;
}

export interface BacktestEquityPoint {
  date: string;
  equity: number;
  cash: number;
  collateral_locked: number;
  unrealized_pnl: number;
  spy_benchmark: number | null;
}

export interface StrategyParamsIn {
  starting_capital?: number;
  max_concurrent_positions?: number;
  dte_target?: number;
  delta_target?: number;
  profit_take_pct?: number;
  manage_dte?: number;
  fee_per_contract?: number;
  slippage_per_share?: number;
  hold_losers_to_expiry?: boolean;
  use_real_chain?: boolean;
}

export interface BacktestRunIn {
  mode?: BacktestMode;
  config_id: number;
  start_date: string;
  end_date: string;
  forward_days?: number;
  symbols?: string[] | null;
  strategy_params?: StrategyParamsIn;
}

export interface BacktestCoverageOut {
  start: string;
  end: string;
  calendar: string;
  trading_days: number;
  symbols_requested: string[];
  symbols_with_any_data: string[];
  symbols_missing: string[];
  symbol_day_pairs_expected: number;
  symbol_day_pairs_present: number;
  coverage_pct: number;
  first_uncovered_day: string | null;
}

export interface BacktestCompareEquityPoint {
  date: string;
  // Server emits {[run_id]: ratio}; pydantic stringifies the int keys.
  runs: Record<string, number>;
  spy_ratio: number | null;
}

export interface BacktestCompareOut {
  runs: BacktestRunOut[];
  common_start: string | null;
  common_end: string | null;
  equity: BacktestCompareEquityPoint[];
}
