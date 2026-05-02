import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchMacroHistory } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { VixHistoryChart } from "@/components/charts/VixHistoryChart";
import { MacroStrip } from "@/components/MacroStrip";
import { UpcomingEarnings } from "@/components/UpcomingEarnings";
import { WatchlistSummary } from "@/components/WatchlistSummary";
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
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>VIX history</CardTitle>
            <p className="text-muted-foreground text-xs">
              Spot VIX vs. 9-day. Dashed lines mark 20 (elevated) and 30 (stress).
            </p>
          </div>
          <div className="border-border/60 inline-flex overflow-hidden rounded-md border text-[11px]">
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
        <Card>
          <CardHeader>
            <CardTitle>Recent ingestion runs</CardTitle>
            <p className="text-muted-foreground text-xs">
              Job history lands once the scheduler is wired up.
            </p>
          </CardHeader>
          <CardContent>
            <div className="border-border/50 text-muted-foreground flex h-32 items-center justify-center rounded-md border border-dashed text-sm">
              No runs recorded yet
            </div>
          </CardContent>
        </Card>
      </div>

      <UpcomingEarnings />
    </div>
  );
}
