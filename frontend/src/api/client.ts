export interface HealthStatus {
  status: string;
  app_env: string;
  server_time_utc: string;
  database_url_scheme: string;
  last_bar_date: string | null;
  bar_count: number;
}

export async function fetchHealth(): Promise<HealthStatus> {
  const response = await fetch("/api/system/health");
  if (!response.ok) {
    throw new Error(`health check failed: ${response.status}`);
  }
  return (await response.json()) as HealthStatus;
}
