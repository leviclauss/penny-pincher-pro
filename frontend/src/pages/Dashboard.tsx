import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { MacroStrip } from "@/components/MacroStrip";
import { UpcomingEarnings } from "@/components/UpcomingEarnings";
import { WatchlistSummary } from "@/components/WatchlistSummary";

export function Dashboard(): JSX.Element {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-muted-foreground text-sm">
          Daily review starting point. Macro context, watchlist freshness, and
          near-term earnings.
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
          <CardContent className="text-muted-foreground text-sm">
            No data yet.
          </CardContent>
        </Card>
      </div>

      <UpcomingEarnings />
    </div>
  );
}
