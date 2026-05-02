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

export interface UpcomingEarning {
  symbol: string;
  name: string | null;
  earnings_date: string;
  time_of_day: string | null;
}
