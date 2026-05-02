import type {
  ChartBar,
  HealthStatus,
  IVPoint,
  MacroPoint,
  TickerSummary,
  UpcomingEarning,
} from "./types";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`request failed (${response.status}): ${path}`);
  }
  return (await response.json()) as T;
}

export function fetchHealth(): Promise<HealthStatus> {
  return getJson<HealthStatus>("/api/system/health");
}

export function fetchTickers(): Promise<TickerSummary[]> {
  return getJson<TickerSummary[]>("/api/tickers");
}

export function fetchTickerChart(symbol: string, range = "1y"): Promise<ChartBar[]> {
  return getJson<ChartBar[]>(`/api/tickers/${encodeURIComponent(symbol)}/chart?range=${range}`);
}

export function fetchTickerIvHistory(symbol: string, range = "1y"): Promise<IVPoint[]> {
  return getJson<IVPoint[]>(
    `/api/tickers/${encodeURIComponent(symbol)}/iv-history?range=${range}`,
  );
}

export function fetchMacroCurrent(): Promise<MacroPoint | null> {
  return getJson<MacroPoint | null>("/api/macro/current");
}

export function fetchMacroHistory(range = "6m"): Promise<MacroPoint[]> {
  return getJson<MacroPoint[]>(`/api/macro/history?range=${range}`);
}

export function fetchUpcomingEarnings(days = 7): Promise<UpcomingEarning[]> {
  return getJson<UpcomingEarning[]>(`/api/earnings/upcoming?days=${days}`);
}
