import type {
  ChartBar,
  HealthStatus,
  IVPoint,
  JobInfoOut,
  JobRunOut,
  MacroPoint,
  TickerCreate,
  TickerPatch,
  TickerSummary,
  TriggerResponse,
  UpcomingEarning,
} from "./types";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`request failed (${response.status}): ${path}`);
  }
  return (await response.json()) as T;
}

async function mutateJson<T>(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T | null> {
  const response = await fetch(path, {
    method,
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let detail = "";
    try {
      const data = (await response.json()) as { detail?: string };
      detail = data?.detail ?? "";
    } catch {
      // ignore parse failure
    }
    throw new Error(
      `request failed (${response.status}): ${method} ${path}${detail ? ` — ${detail}` : ""}`,
    );
  }
  if (response.status === 204) return null;
  return (await response.json()) as T;
}

export function fetchHealth(): Promise<HealthStatus> {
  return getJson<HealthStatus>("/api/system/health");
}

export function fetchTickers(includeHidden = false): Promise<TickerSummary[]> {
  const qs = includeHidden ? "?include_hidden=true" : "";
  return getJson<TickerSummary[]>(`/api/tickers${qs}`);
}

export function createTicker(input: TickerCreate): Promise<TickerSummary> {
  return mutateJson<TickerSummary>("POST", "/api/tickers", input).then(
    (r) => r as TickerSummary,
  );
}

export function patchTicker(symbol: string, patch: TickerPatch): Promise<TickerSummary> {
  return mutateJson<TickerSummary>(
    "PATCH",
    `/api/tickers/${encodeURIComponent(symbol)}`,
    patch,
  ).then((r) => r as TickerSummary);
}

export function deleteTicker(symbol: string): Promise<void> {
  return mutateJson<void>("DELETE", `/api/tickers/${encodeURIComponent(symbol)}`).then(
    () => undefined,
  );
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

export function fetchJobRuns(jobName: string, limit = 5): Promise<JobRunOut[]> {
  return getJson<JobRunOut[]>(
    `/api/system/job-runs?job_name=${encodeURIComponent(jobName)}&limit=${limit}`,
  );
}

export function fetchAllJobRuns(limit = 5): Promise<JobRunOut[]> {
  return getJson<JobRunOut[]>(`/api/system/job-runs?limit=${limit}`);
}

export function fetchJobs(): Promise<JobInfoOut[]> {
  return getJson<JobInfoOut[]>("/api/system/jobs");
}

export function triggerJob(name: string): Promise<TriggerResponse> {
  return mutateJson<TriggerResponse>(
    "POST",
    `/api/system/jobs/${encodeURIComponent(name)}/run`,
  ).then((r) => r as TriggerResponse);
}
