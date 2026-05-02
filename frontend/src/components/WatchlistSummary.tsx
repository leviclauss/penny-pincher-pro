import { useQuery } from "@tanstack/react-query";
import { Database, Eye, Server } from "lucide-react";
import { fetchHealth, fetchTickers } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

function Stat({
  icon: Icon,
  label,
  value,
  hint,
  tone = "neutral",
}: {
  icon: typeof Eye;
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "neutral" | "ok" | "bad";
}): JSX.Element {
  return (
    <div className="border-border/60 bg-background/40 flex items-start gap-3 rounded-md border p-3">
      <div
        className={cn(
          "flex h-8 w-8 items-center justify-center rounded-md",
          tone === "ok" && "bg-emerald-500/15 text-emerald-300",
          tone === "bad" && "bg-red-500/15 text-red-300",
          tone === "neutral" && "bg-muted text-muted-foreground",
        )}
      >
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-muted-foreground text-[10px] font-medium uppercase tracking-widest">
          {label}
        </div>
        <div className="text-foreground truncate text-lg font-semibold">{value}</div>
        {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
      </div>
    </div>
  );
}

export function WatchlistSummary(): JSX.Element {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
  });
  const tickers = useQuery({ queryKey: ["tickers"], queryFn: fetchTickers });

  const total = tickers.data?.length ?? 0;
  const active = tickers.data?.filter((t) => t.is_active).length ?? 0;
  const ok = health.data?.status === "ok" && !health.isError;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Watchlist</CardTitle>
        <p className="text-muted-foreground text-xs">
          Backend health and ingestion freshness
        </p>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Stat
          icon={Eye}
          label="Active tickers"
          value={active}
          hint={`${total} total`}
        />
        <Stat
          icon={Database}
          label="Last bar"
          value={
            <span className="font-mono text-base">
              {formatDate(health.data?.last_bar_date)}
            </span>
          }
          hint={`${(health.data?.bar_count ?? 0).toLocaleString()} bars stored`}
        />
        <Stat
          icon={Server}
          label="API status"
          value={
            health.isError ? (
              <span className="text-destructive">unreachable</span>
            ) : (
              health.data?.status ?? "—"
            )
          }
          hint={health.data?.app_env ?? ""}
          tone={ok ? "ok" : "bad"}
        />
      </CardContent>
    </Card>
  );
}
