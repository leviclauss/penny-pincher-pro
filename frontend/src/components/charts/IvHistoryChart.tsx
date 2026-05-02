import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { IVPoint } from "@/api/types";
import { formatDate, formatDateShort } from "@/lib/format";

const AXIS_COLOR = "hsl(240 5% 50%)";
const GRID_COLOR = "hsl(240 5% 16% / 0.7)";
const RANK_MIN_HISTORY = 126;

export function IvHistoryChart({ points }: { points: IVPoint[] }): JSX.Element {
  const data = points
    .filter((p) => p.iv_atm !== null)
    .map((p) => ({ date: p.date, iv_atm: p.iv_atm === null ? null : p.iv_atm * 100 }));

  if (data.length === 0) {
    return (
      <div className="border-border/50 text-muted-foreground flex h-[220px] items-center justify-center rounded-md border border-dashed text-sm">
        No IV history yet — options ingestion needs to run for at least one day.
      </div>
    );
  }

  const belowThreshold = data.length < RANK_MIN_HISTORY;
  const progressPct = Math.min(100, (data.length / RANK_MIN_HISTORY) * 100);

  return (
    <div className="space-y-3">
      {belowThreshold && (
        <div className="border-border/60 bg-card/40 rounded-md border border-dashed p-3 text-xs">
          <div className="text-muted-foreground flex items-baseline justify-between gap-3">
            <span>
              Building IV history —{" "}
              <span className="text-foreground font-medium tabular-nums">
                {data.length}
              </span>{" "}
              / {RANK_MIN_HISTORY} days collected.
            </span>
            <span className="font-mono">{progressPct.toFixed(0)}%</span>
          </div>
          <div className="bg-border/40 mt-2 h-1 overflow-hidden rounded-full">
            <div
              className="bg-cyan-400/70 h-full rounded-full transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <p className="text-muted-foreground mt-2 leading-relaxed">
            Alpaca's options history is shallow, so IV accumulates one day per
            pipeline run. IV rank and percentile stay null until the threshold
            is reached.
          </p>
        </div>
      )}
      <div className="h-[240px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="ivGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.45} />
              <stop offset="100%" stopColor="#22d3ee" stopOpacity={0.02} />
            </linearGradient>
          </defs>
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
            width={60}
            tickFormatter={(v: number) => `${v.toFixed(0)}%`}
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
              typeof value === "number" ? `${value.toFixed(1)}%` : value
            }
          />
          <Area
            type="monotone"
            dataKey="iv_atm"
            stroke="#22d3ee"
            strokeWidth={1.75}
            fill="url(#ivGradient)"
            isAnimationActive={false}
            name="IV ATM"
          />
        </AreaChart>
      </ResponsiveContainer>
      </div>
    </div>
  );
}
