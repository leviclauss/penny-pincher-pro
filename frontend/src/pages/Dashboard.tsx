import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { MacroStrip } from "@/components/MacroStrip";
import { UpcomingEarnings } from "@/components/UpcomingEarnings";
import { WatchlistSummary } from "@/components/WatchlistSummary";

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
