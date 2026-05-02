import { useQuery } from "@tanstack/react-query";
import { fetchMacroCurrent } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { formatNumber } from "@/lib/format";

function Pill({ tone, children }: { tone: "up" | "down" | "neutral"; children: React.ReactNode }): JSX.Element {
  const colors: Record<typeof tone, string> = {
    up: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    down: "bg-red-500/15 text-red-700 dark:text-red-300",
    neutral: "bg-muted text-muted-foreground",
  };
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", colors[tone])}>
      {children}
    </span>
  );
}

export function MacroStrip(): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["macro", "current"],
    queryFn: fetchMacroCurrent,
  });

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Macro</CardTitle>
        </CardHeader>
        <CardContent className="text-muted-foreground text-sm">Loading…</CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Macro</CardTitle>
        </CardHeader>
        <CardContent className="text-destructive text-sm">Failed to load macro data.</CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Macro</CardTitle>
        </CardHeader>
        <CardContent className="text-muted-foreground text-sm">
          No macro data yet. Run ingestion to populate VIX / SPY.
        </CardContent>
      </Card>
    );
  }

  const term = data.vix_term_structure;
  const termTone: "up" | "down" | "neutral" =
    term === null ? "neutral" : term < 1 ? "down" : "up";
  const termLabel = term === null ? "—" : term < 1 ? "Backwardation" : "Contango";
  const spyTone: "up" | "down" | "neutral" =
    data.spy_above_200ema === null ? "neutral" : data.spy_above_200ema ? "up" : "down";
  const spyLabel =
    data.spy_above_200ema === null
      ? "—"
      : data.spy_above_200ema
        ? "Above 200 EMA"
        : "Below 200 EMA";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Macro</CardTitle>
        <p className="text-muted-foreground text-xs">As of {data.date}</p>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <div className="text-muted-foreground text-xs uppercase">VIX</div>
          <div className="text-xl font-semibold">{formatNumber(data.vix_close)}</div>
        </div>
        <div>
          <div className="text-muted-foreground text-xs uppercase">VIX9D</div>
          <div className="text-xl font-semibold">{formatNumber(data.vix_9d)}</div>
        </div>
        <div>
          <div className="text-muted-foreground text-xs uppercase">Term Structure</div>
          <div className="flex items-center gap-2">
            <span className="text-xl font-semibold">{formatNumber(term, 3)}</span>
            <Pill tone={termTone}>{termLabel}</Pill>
          </div>
        </div>
        <div>
          <div className="text-muted-foreground text-xs uppercase">SPY Regime</div>
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "inline-block h-3 w-3 rounded-full",
                spyTone === "up" && "bg-emerald-500",
                spyTone === "down" && "bg-red-500",
                spyTone === "neutral" && "bg-muted-foreground/40",
              )}
            />
            <span className="text-sm">{spyLabel}</span>
          </div>
          <div className="text-muted-foreground mt-1 text-xs">
            {formatNumber(data.spy_close)} / EMA {formatNumber(data.spy_ema_200)}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
