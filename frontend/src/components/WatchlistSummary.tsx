import { useQuery } from "@tanstack/react-query";
import { fetchHealth, fetchTickers } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

export function WatchlistSummary(): JSX.Element {
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 30_000 });
  const tickers = useQuery({ queryKey: ["tickers"], queryFn: fetchTickers });

  const total = tickers.data?.length ?? 0;
  const active = tickers.data?.filter((t) => t.is_active).length ?? 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Watchlist</CardTitle>
        <p className="text-muted-foreground text-xs">Backend health + ingestion freshness</p>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <div className="text-muted-foreground text-xs uppercase">Active tickers</div>
          <div className="text-xl font-semibold">{active}</div>
          <div className="text-muted-foreground text-xs">{total} total</div>
        </div>
        <div>
          <div className="text-muted-foreground text-xs uppercase">Last bar date</div>
          <div className="font-mono text-base">{health.data?.last_bar_date ?? "—"}</div>
        </div>
        <div>
          <div className="text-muted-foreground text-xs uppercase">Bars stored</div>
          <div className="text-xl font-semibold">
            {(health.data?.bar_count ?? 0).toLocaleString()}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground text-xs uppercase">Status</div>
          <div className="text-base">
            {health.isError
              ? <span className="text-destructive">unreachable</span>
              : health.data?.status ?? "—"}
          </div>
          <div className="text-muted-foreground text-xs">{health.data?.app_env ?? ""}</div>
        </div>
      </CardContent>
    </Card>
  );
}
