import type {
  AssignInput,
  CalledAwayInput,
  ChartBar,
  CloseDebitInput,
  CloseSharesInput,
  ExpireInput,
  HealthStatus,
  IVPoint,
  JobInfoOut,
  JobRunOut,
  MacroPoint,
  OpenCoveredCallInput,
  OpenShortPutInput,
  PositionAttributionOut,
  PositionOut,
  PositionState,
  ScreenerConfigDetail,
  ScreenerConfigSummary,
  ScreenerConfigWriteIn,
  ScreenerResultsResponse,
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

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function mutateJson<T>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T | null> {
  const response = await fetch(path, {
    method,
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let detail: unknown = null;
    try {
      const data = (await response.json()) as { detail?: unknown };
      detail = data?.detail ?? null;
    } catch {
      // ignore parse failure
    }
    const detailText =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message: unknown }).message)
          : "";
    throw new ApiError(
      response.status,
      detail,
      `request failed (${response.status}): ${method} ${path}${detailText ? ` — ${detailText}` : ""}`,
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

export function fetchScreenerConfigs(activeOnly = false): Promise<ScreenerConfigSummary[]> {
  const qs = activeOnly ? "?active_only=true" : "";
  return getJson<ScreenerConfigSummary[]>(`/api/screener/configs${qs}`);
}

export function fetchScreenerConfig(configId: number): Promise<ScreenerConfigDetail> {
  return getJson<ScreenerConfigDetail>(`/api/screener/configs/${configId}`);
}

export function createScreenerConfig(
  payload: ScreenerConfigWriteIn,
): Promise<ScreenerConfigDetail> {
  return mutateJson<ScreenerConfigDetail>("POST", "/api/screener/configs", payload).then(
    (r) => r as ScreenerConfigDetail,
  );
}

export function updateScreenerConfig(
  configId: number,
  payload: ScreenerConfigWriteIn,
): Promise<ScreenerConfigDetail> {
  return mutateJson<ScreenerConfigDetail>(
    "PUT",
    `/api/screener/configs/${configId}`,
    payload,
  ).then((r) => r as ScreenerConfigDetail);
}

export function patchScreenerConfigActive(
  configId: number,
  isActive: boolean,
): Promise<ScreenerConfigDetail> {
  return mutateJson<ScreenerConfigDetail>(
    "PATCH",
    `/api/screener/configs/${configId}/active`,
    { is_active: isActive },
  ).then((r) => r as ScreenerConfigDetail);
}

export function deleteScreenerConfig(configId: number, cascade = false): Promise<void> {
  const qs = cascade ? "?cascade=true" : "";
  return mutateJson<void>("DELETE", `/api/screener/configs/${configId}${qs}`).then(
    () => undefined,
  );
}

export interface ScreenerResultsParams {
  configId?: number | null;
  date?: string | null;
  passedOnly?: boolean;
  limit?: number;
}

export function fetchScreenerResults(
  params: ScreenerResultsParams = {},
): Promise<ScreenerResultsResponse> {
  const qs = new URLSearchParams();
  if (params.configId != null) qs.set("config_id", String(params.configId));
  if (params.date) qs.set("date", params.date);
  if (params.passedOnly !== undefined) qs.set("passed_only", String(params.passedOnly));
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  const suffix = qs.toString();
  return getJson<ScreenerResultsResponse>(
    `/api/screener/results${suffix ? `?${suffix}` : ""}`,
  );
}

export interface PositionsListParams {
  state?: PositionState | null;
  symbol?: string | null;
}

export function fetchPositions(params: PositionsListParams = {}): Promise<PositionOut[]> {
  const qs = new URLSearchParams();
  if (params.state) qs.set("state", params.state);
  if (params.symbol) qs.set("symbol", params.symbol);
  const suffix = qs.toString();
  return getJson<PositionOut[]>(`/api/positions${suffix ? `?${suffix}` : ""}`);
}

export function fetchPosition(positionId: number): Promise<PositionOut> {
  return getJson<PositionOut>(`/api/positions/${positionId}`);
}

export function fetchPositionAttribution(
  positionId: number,
): Promise<PositionAttributionOut> {
  return getJson<PositionAttributionOut>(`/api/positions/${positionId}/attribution`);
}

export function openShortPut(input: OpenShortPutInput): Promise<PositionOut> {
  return mutateJson<PositionOut>("POST", "/api/positions/short-put", input).then(
    (r) => r as PositionOut,
  );
}

export function patchPosition(
  positionId: number,
  patch: { notes?: string | null },
): Promise<PositionOut> {
  return mutateJson<PositionOut>("PATCH", `/api/positions/${positionId}`, patch).then(
    (r) => r as PositionOut,
  );
}

function postTransition<TBody>(
  positionId: number,
  action: string,
  body: TBody,
): Promise<PositionOut> {
  return mutateJson<PositionOut>(
    "POST",
    `/api/positions/${positionId}/${action}`,
    body,
  ).then((r) => r as PositionOut);
}

export const closeShortPut = (id: number, body: CloseDebitInput): Promise<PositionOut> =>
  postTransition(id, "close-put", body);

export const expireShortPut = (id: number, body: ExpireInput): Promise<PositionOut> =>
  postTransition(id, "expire-put", body);

export const assignShortPut = (id: number, body: AssignInput): Promise<PositionOut> =>
  postTransition(id, "assign-put", body);

export const openCoveredCall = (
  id: number,
  body: OpenCoveredCallInput,
): Promise<PositionOut> => postTransition(id, "covered-call", body);

export const closeCoveredCall = (
  id: number,
  body: CloseDebitInput,
): Promise<PositionOut> => postTransition(id, "close-call", body);

export const expireCoveredCall = (
  id: number,
  body: ExpireInput,
): Promise<PositionOut> => postTransition(id, "expire-call", body);

export const calledAway = (id: number, body: CalledAwayInput): Promise<PositionOut> =>
  postTransition(id, "called-away", body);

export const closeShares = (id: number, body: CloseSharesInput): Promise<PositionOut> =>
  postTransition(id, "close-shares", body);
