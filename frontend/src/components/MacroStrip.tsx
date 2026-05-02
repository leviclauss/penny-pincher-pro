import { useQuery } from "@tanstack/react-query";
import { Activity, Gauge, LineChart, ShieldCheck } from "lucide-react";
import { fetchMacroCurrent } from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { formatDate, formatNumber } from "@/lib/format";

function Pill({
  tone,
  children,
}: {
  tone: "up" | "down" | "neutral";
  children: React.ReactNode;
}): JSX.Element {
  const colors: Record<typeof tone, string> = {
    up: "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30",
    down: "bg-red-500/15 text-red-300 ring-1 ring-red-500/30",
    neutral: "bg-muted text-muted-foreground ring-1 ring-border",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider",
        colors[tone],
      )}
    >
      {children}
    </span>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  hint,
  pill,
}: {
  icon: typeof Activity;
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  pill?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="border-border/60 bg-background/40 flex flex-col gap-1.5 rounded-md border p-3">
      <div className="text-muted-foreground flex items-center gap-2 text-[10px] font-medium uppercase tracking-widest">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-foreground text-2xl font-semibold tracking-tight">
          {value}
        </span>
        {pill}
      </div>
      {hint && <div className="text-muted-foreground text-xs">{hint}</div>}
    </div>
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
        <CardContent className="text-destructive text-sm">
          Failed to load macro data.
        </CardContent>
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
        ? "Risk-on"
        : "Risk-off";

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Market context</CardTitle>
          <span className="text-muted-foreground font-mono text-[11px]">
            {formatDate(data.date)}
          </span>
        </div>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat icon={Activity} label="VIX" value={formatNumber(data.vix_close)} />
        <Stat icon={Gauge} label="VIX9D" value={formatNumber(data.vix_9d)} />
        <Stat
          icon={LineChart}
          label="Term structure"
          value={formatNumber(term, 3)}
          pill={<Pill tone={termTone}>{termLabel}</Pill>}
        />
        <Stat
          icon={ShieldCheck}
          label="SPY regime"
          value={
            <span className="flex items-center gap-2">
              <span
                className={cn(
                  "inline-block h-2.5 w-2.5 rounded-full",
                  spyTone === "up" && "bg-emerald-400 shadow-[0_0_12px] shadow-emerald-400/60",
                  spyTone === "down" && "bg-red-500 shadow-[0_0_12px] shadow-red-500/60",
                  spyTone === "neutral" && "bg-muted-foreground/40",
                )}
              />
              <span className="text-base">{spyLabel}</span>
            </span>
          }
          hint={
            <>
              {formatNumber(data.spy_close)} <span className="opacity-50">·</span> EMA{" "}
              {formatNumber(data.spy_ema_200)}
            </>
          }
        />
      </CardContent>
    </Card>
  );
}
