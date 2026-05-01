import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "@/api/client";

export function App(): JSX.Element {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  });

  return (
    <main className="min-h-screen p-8">
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Penny Pincher Pro</h1>
        <p className="text-muted-foreground text-sm">
          Wheel options screener — frontend skeleton
        </p>
      </header>

      <section className="rounded-lg border border-border p-4">
        <h2 className="mb-3 text-lg font-medium">Backend health</h2>
        {isLoading && <p className="text-muted-foreground">Checking…</p>}
        {isError && (
          <p className="text-destructive">
            Backend unreachable: {error instanceof Error ? error.message : "unknown error"}
          </p>
        )}
        {data && (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted-foreground">Status</dt>
            <dd>{data.status}</dd>
            <dt className="text-muted-foreground">Environment</dt>
            <dd>{data.app_env}</dd>
            <dt className="text-muted-foreground">Server time (UTC)</dt>
            <dd>{data.server_time_utc}</dd>
            <dt className="text-muted-foreground">Database</dt>
            <dd>{data.database_url_scheme}</dd>
            <dt className="text-muted-foreground">Last bar date</dt>
            <dd>{data.last_bar_date ?? "—"}</dd>
            <dt className="text-muted-foreground">Bars stored</dt>
            <dd>{data.bar_count.toLocaleString()}</dd>
          </dl>
        )}
      </section>
    </main>
  );
}
