import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowLeft, FlaskConical, Loader2 } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchBacktestCompare } from "@/api/client";
import type {
  BacktestCompareEquityPoint,
  BacktestCompareOut,
  BacktestMetrics,
  BacktestRunOut,
} from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { formatDate, formatDateShort } from "@/lib/format";
import { cn } from "@/lib/utils";

const RUN_COLORS = ["#34d399", "#60a5fa", "#f472b6"];
const SPY_COLOR = "#94a3b8";
const AXIS_COLOR = "hsl(240 5% 50%)";
const GRID_COLOR = "hsl(240 5% 16% / 0.7)";

type MetricKey = keyof BacktestMetrics;

type MetricRow = {
  key: MetricKey;
  label: string;
  format: (v: number | null | undefined) => string;
  // higher = better; lower = better; null = neutral (no highlight).
  bias: "higher" | "lower" | null;
};

const METRIC_ROWS: MetricRow[] = [
  { key: "sharpe", label: "Sharpe", format: fmtRatio, bias: "higher" },
  { key: "sortino", label: "Sortino", format: fmtRatio, bias: "higher" },
  {
    key: "max_drawdown_pct",
    label: "Max drawdown",
    format: fmtPct,
    bias: "higher", // less negative is better
  },
  { key: "cagr", label: "CAGR", format: fmtPct, bias: "higher" },
  { key: "win_rate", label: "Win rate", format: fmtFraction, bias: "higher" },
  { key: "profit_factor", label: "Profit factor", format: fmtRatio, bias: "higher" },
  { key: "expectancy", label: "Expectancy / trade", format: fmtMoney, bias: "higher" },
  { key: "avg_win", label: "Avg win", format: fmtMoney, bias: "higher" },
  { key: "avg_loss", label: "Avg loss", format: fmtMoney, bias: "higher" },
  { key: "cycles_completed", label: "Cycles completed", format: fmtInt, bias: null },
  {
    key: "assignment_rate",
    label: "Assignment rate",
    format: fmtFraction,
    bias: null,
  },
  { key: "avg_dte_held", label: "Avg DTE held", format: fmtDays, bias: null },
];

function fmtRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(2);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function fmtFraction(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function fmtInt(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString();
}

function fmtDays(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)}d`;
}

export function BacktestCompare(): JSX.Element {
  const [params] = useSearchParams();
  const ids = useMemo(() => parseRunIds(params.get("ids")), [params]);

  if (ids.length === 0) {
    return (
      <div className="space-y-6">
        <Header />
        <Card>
          <CardContent className="text-muted-foreground px-5 py-8 text-sm">
            No runs selected. Pick 2 or 3 strategy runs from the{" "}
            <Link to="/backtest" className="underline">
              Backtest page
            </Link>{" "}
            and click "Compare selected".
          </CardContent>
        </Card>
      </div>
    );
  }

  return <CompareView runIds={ids} />;
}

function CompareView({ runIds }: { runIds: number[] }): JSX.Element {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["backtest-compare", runIds],
    queryFn: () => fetchBacktestCompare(runIds),
  });

  return (
    <div className="space-y-6">
      <Header />
      {isLoading && (
        <Card>
          <CardContent className="text-muted-foreground flex items-center gap-2 px-5 py-8 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading runs…
          </CardContent>
        </Card>
      )}
      {isError && (
        <Card>
          <CardContent className="text-destructive px-5 py-8 text-sm">
            {error instanceof Error ? error.message : "Failed to load comparison."}
          </CardContent>
        </Card>
      )}
      {data && <CompareBody data={data} runIds={runIds} />}
    </div>
  );
}

function CompareBody({
  data,
  runIds,
}: {
  data: BacktestCompareOut;
  runIds: number[];
}): JSX.Element {
  const orderedRuns = useMemo(() => orderRuns(data.runs, runIds), [data.runs, runIds]);
  const colorByRun = useMemo(() => {
    const out = new Map<number, string>();
    orderedRuns.forEach((r, i) => out.set(r.id, RUN_COLORS[i % RUN_COLORS.length]));
    return out;
  }, [orderedRuns]);

  return (
    <>
      <Card>
        <CardHeader className="px-3 sm:px-5">
          <CardTitle>Equity overlay (normalized to 1.0)</CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-3 sm:px-5 sm:pb-5">
          {data.common_start && data.common_end ? (
            <p className="text-muted-foreground mb-3 text-xs">
              Common window: {formatDate(data.common_start)} →{" "}
              {formatDate(data.common_end)}. Each curve is plotted as{" "}
              <code>equity / starting_capital</code>.
            </p>
          ) : (
            <p className="text-muted-foreground mb-3 text-xs">
              Runs share no overlapping date range — equity overlay disabled.
            </p>
          )}
          <CompareEquityChart points={data.equity} runs={orderedRuns} colors={colorByRun} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="px-3 sm:px-5">
          <CardTitle>Metrics</CardTitle>
        </CardHeader>
        <CardContent className="px-0 pb-3 sm:pb-5">
          <MetricsTable runs={orderedRuns} colors={colorByRun} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="px-3 sm:px-5">
          <CardTitle>Param diff</CardTitle>
        </CardHeader>
        <CardContent className="px-0 pb-3 sm:pb-5">
          <ParamDiffTable runs={orderedRuns} colors={colorByRun} />
        </CardContent>
      </Card>
    </>
  );
}

function CompareEquityChart({
  points,
  runs,
  colors,
}: {
  points: BacktestCompareEquityPoint[];
  runs: BacktestRunOut[];
  colors: Map<number, string>;
}): JSX.Element {
  if (points.length === 0) {
    return (
      <div className="border-border/50 text-muted-foreground flex h-[300px] items-center justify-center rounded-md border border-dashed text-sm">
        No overlapping equity data to plot.
      </div>
    );
  }

  const data = points.map((p) => {
    const row: Record<string, number | string | null> = {
      date: p.date,
      spy: p.spy_ratio,
    };
    for (const run of runs) {
      row[`run_${run.id}`] = p.runs[String(run.id)] ?? null;
    }
    return row;
  });
  const hasSpy = points.some((p) => p.spy_ratio != null);

  return (
    <div className="h-[320px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            axisLine={{ stroke: GRID_COLOR }}
            tickLine={{ stroke: GRID_COLOR }}
            minTickGap={48}
            tickFormatter={(v: string) => formatDateShort(v)}
          />
          <YAxis
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            axisLine={{ stroke: GRID_COLOR }}
            tickLine={{ stroke: GRID_COLOR }}
            domain={["auto", "auto"]}
            width={56}
            tickFormatter={(v: number) => `${v.toFixed(2)}x`}
          />
          <ReferenceLine
            y={1.0}
            stroke={AXIS_COLOR}
            strokeDasharray="3 3"
            label={{
              value: "starting capital",
              position: "insideTopLeft",
              fill: AXIS_COLOR,
              fontSize: 10,
            }}
          />
          <Tooltip
            cursor={{ stroke: "hsl(var(--primary) / 0.4)", strokeDasharray: "3 3" }}
            contentStyle={{
              backgroundColor: "hsl(240 6% 9% / 0.95)",
              border: "1px solid hsl(var(--border))",
              borderRadius: 8,
              fontSize: 12,
              color: "hsl(var(--foreground))",
              boxShadow: "0 8px 24px hsl(0 0% 0% / 0.5)",
            }}
            labelStyle={{ color: "hsl(var(--muted-foreground))", marginBottom: 4 }}
            labelFormatter={(label: string) => formatDate(label)}
            formatter={(value: number | string) =>
              typeof value === "number" ? `${value.toFixed(3)}x` : value
            }
          />
          <Legend
            verticalAlign="top"
            align="right"
            iconType="line"
            iconSize={12}
            wrapperStyle={{ fontSize: 11, paddingBottom: 4 }}
          />
          {runs.map((run) => (
            <Line
              key={run.id}
              type="monotone"
              dataKey={`run_${run.id}`}
              stroke={colors.get(run.id) ?? RUN_COLORS[0]}
              strokeWidth={1.75}
              dot={false}
              isAnimationActive={false}
              connectNulls
              name={runLabel(run)}
            />
          ))}
          {hasSpy && (
            <Line
              type="monotone"
              dataKey="spy"
              stroke={SPY_COLOR}
              strokeWidth={1.25}
              strokeDasharray="4 3"
              dot={false}
              isAnimationActive={false}
              connectNulls
              name="SPY (buy & hold)"
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function MetricsTable({
  runs,
  colors,
}: {
  runs: BacktestRunOut[];
  colors: Map<number, string>;
}): JSX.Element {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Metric</TableHead>
            {runs.map((run) => (
              <TableHead key={run.id} className="text-right">
                <span
                  className="inline-flex items-center gap-1.5"
                  style={{ color: colors.get(run.id) }}
                >
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ backgroundColor: colors.get(run.id) }}
                  />
                  {runLabel(run)}
                </span>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {METRIC_ROWS.map((row) => {
            const values = runs.map((run) =>
              run.metrics ? (run.metrics[row.key] as number | null | undefined) : null,
            );
            const bestIndex = bestForBias(values, row.bias);
            return (
              <TableRow key={row.key}>
                <TableCell className="font-medium">{row.label}</TableCell>
                {runs.map((run, idx) => {
                  const v = values[idx];
                  const isBest = idx === bestIndex && row.bias != null;
                  return (
                    <TableCell
                      key={run.id}
                      className={cn(
                        "text-right font-mono",
                        isBest && "text-emerald-500",
                      )}
                    >
                      {row.format(v)}
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
          <TableRow>
            <TableCell className="font-medium">Total return</TableCell>
            {runs.map((run, idx) => {
              const values = runs.map((r) => r.total_return_pct);
              const bestIndex = bestForBias(values, "higher");
              const isBest = idx === bestIndex;
              return (
                <TableCell
                  key={run.id}
                  className={cn(
                    "text-right font-mono",
                    isBest && "text-emerald-500",
                  )}
                >
                  {fmtPct(run.total_return_pct)}
                </TableCell>
              );
            })}
          </TableRow>
        </TableBody>
      </Table>
    </div>
  );
}

function ParamDiffTable({
  runs,
  colors,
}: {
  runs: BacktestRunOut[];
  colors: Map<number, string>;
}): JSX.Element {
  // Union of all keys across the runs, preserving deterministic order.
  const keys = useMemo(() => {
    const seen = new Set<string>();
    const ordered: string[] = [];
    for (const run of runs) {
      for (const k of Object.keys(run.params_json ?? {})) {
        if (!seen.has(k)) {
          seen.add(k);
          ordered.push(k);
        }
      }
    }
    return ordered.sort();
  }, [runs]);

  if (keys.length === 0) {
    return (
      <div className="text-muted-foreground px-5 py-4 text-sm">
        No params recorded.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Param</TableHead>
            {runs.map((run) => (
              <TableHead key={run.id} className="text-right">
                <span style={{ color: colors.get(run.id) }}>{runLabel(run)}</span>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {keys.map((key) => {
            const values = runs.map((run) => (run.params_json ?? {})[key]);
            const allEqual = values.every((v) => stableStringify(v) === stableStringify(values[0]));
            return (
              <TableRow key={key}>
                <TableCell className="font-mono text-xs">{key}</TableCell>
                {values.map((v, idx) => (
                  <TableCell
                    key={runs[idx].id}
                    className={cn(
                      "text-right font-mono text-xs",
                      !allEqual && "text-amber-500",
                    )}
                  >
                    {formatParamValue(v)}
                  </TableCell>
                ))}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function Header(): JSX.Element {
  return (
    <header className="space-y-1.5">
      <p className="text-primary text-xs font-semibold uppercase tracking-widest">
        Analysis
      </p>
      <h1 className="flex items-center gap-2.5 text-3xl font-semibold tracking-tight">
        <FlaskConical className="h-7 w-7" />
        Backtest comparison
      </h1>
      <p>
        <Link
          to="/backtest"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to runs
        </Link>
      </p>
    </header>
  );
}

function parseRunIds(raw: string | null): number[] {
  if (!raw) return [];
  const out: number[] = [];
  const seen = new Set<number>();
  for (const token of raw.split(",")) {
    const trimmed = token.trim();
    if (!trimmed) continue;
    const n = Number.parseInt(trimmed, 10);
    if (!Number.isFinite(n) || seen.has(n)) continue;
    out.push(n);
    seen.add(n);
    if (out.length >= 3) break;
  }
  return out;
}

function orderRuns(runs: BacktestRunOut[], ids: number[]): BacktestRunOut[] {
  const byId = new Map(runs.map((r) => [r.id, r]));
  const out: BacktestRunOut[] = [];
  for (const id of ids) {
    const run = byId.get(id);
    if (run) out.push(run);
  }
  return out;
}

function bestForBias(
  values: Array<number | null | undefined>,
  bias: "higher" | "lower" | null,
): number {
  if (bias == null) return -1;
  let bestIdx = -1;
  let bestValue: number | null = null;
  values.forEach((v, idx) => {
    if (v == null || !Number.isFinite(v)) return;
    if (bestValue == null) {
      bestIdx = idx;
      bestValue = v;
      return;
    }
    if (bias === "higher" && v > bestValue) {
      bestIdx = idx;
      bestValue = v;
    } else if (bias === "lower" && v < bestValue) {
      bestIdx = idx;
      bestValue = v;
    }
  });
  return bestIdx;
}

function runLabel(run: BacktestRunOut): string {
  return `${run.config_name ?? `config #${run.config_id}`} · run #${run.id}`;
}

function formatParamValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") {
    return Number.isInteger(v) ? v.toString() : v.toFixed(4);
  }
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return `[${v.length}]`;
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function stableStringify(v: unknown): string {
  if (v == null) return "null";
  if (typeof v === "object") {
    return JSON.stringify(v);
  }
  return String(v);
}
