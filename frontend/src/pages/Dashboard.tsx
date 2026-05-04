import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { fetchAllJobRuns, fetchMacroHistory } from "@/api/client";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { VixHistoryChart } from "@/components/charts/VixHistoryChart";
import { MacroStrip } from "@/components/MacroStrip";
import { UpcomingEarnings } from "@/components/UpcomingEarnings";
import { WatchlistSummary } from "@/components/WatchlistSummary";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const VIX_RANGES = ["3m", "6m", "1y", "2y"] as const;
type VixRange = (typeof VIX_RANGES)[number];

function VixHistoryCard(): JSX.Element {
  const [range, setRange] = useState<VixRange>("6m");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["macro", "history", range],
    queryFn: () => fetchMacroHistory(range),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2 sm:items-center">
          <div>
            <CardTitle>VIX history</CardTitle>
            <p className="text-muted-foreground text-xs">
              Spot VIX vs. 9-day. Dashed lines mark 20 (elevated) and 30 (stress).
            </p>
          </div>
          <div className="border-border/60 inline-flex shrink-0 overflow-hidden rounded-md border text-[11px]">
            {VIX_RANGES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRange(r)}
                className={cn(
                  "px-2 py-1 font-medium uppercase tracking-wider transition-colors",
                  range === r
                    ? "bg-primary/15 text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {r}
              </button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="text-muted-foreground flex h-[260px] items-center justify-center text-sm">
            Loading…
          </div>
        ) : isError ? (
          <div className="text-destructive flex h-[260px] items-center justify-center text-sm">
            Failed to load VIX history.
          </div>
        ) : (
          <VixHistoryChart points={data ?? []} />
        )}
      </CardContent>
    </Card>
  );
}

function RecentJobRunsCard(): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["job-runs", "recent"],
    queryFn: () => fetchAllJobRuns(5),
    refetchInterval: 30_000,
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Recent ingestion runs</CardTitle>
            <p className="text-muted-foreground text-xs">
              Last five executions across all jobs.
            </p>
          </div>
          <Link
            to="/jobs"
            className="text-primary text-xs font-medium hover:underline"
          >
            View all →
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
            Loading…
          </div>
        ) : isError ? (
          <div className="text-destructive flex h-32 items-center justify-center text-sm">
            Failed to load runs.
          </div>
        ) : !data || data.length === 0 ? (
          <div className="border-border/50 text-muted-foreground flex h-32 items-center justify-center rounded-md border border-dashed text-sm">
            No runs recorded yet
          </div>
        ) : (
          <ul className="divide-border/40 divide-y text-sm">
            {data.map((run) => (
              <li key={run.id} className="flex items-center justify-between gap-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{run.job_name}</div>
                  <div className="text-muted-foreground font-mono text-[11px]">
                    {formatDateTime(run.started_at)}
                  </div>
                </div>
                <Badge
                  variant={
                    run.status === "success"
                      ? "success"
                      : run.status === "failure"
                        ? "destructive"
                        : "warning"
                  }
                >
                  {run.status}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function Dashboard(): JSX.Element {
  return (
    <div className="space-y-8">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          Overview
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          Macro context, watchlist freshness, and near-term earnings — your daily
          starting point.
        </p>
      </header>

      <MacroStrip />

      <VixHistoryCard />

      <div className="grid gap-6 lg:grid-cols-2">
        <WatchlistSummary />
        <RecentJobRunsCard />
      </div>

      <UpcomingEarnings />
    </div>
  );
}
