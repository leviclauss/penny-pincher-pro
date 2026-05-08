import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BacktestEquityPoint } from "@/api/types";
import { formatDate, formatDateShort } from "@/lib/format";

const AXIS_COLOR = "hsl(240 5% 50%)";
const GRID_COLOR = "hsl(240 5% 16% / 0.7)";

export function DrawdownChart({
  points,
}: {
  points: BacktestEquityPoint[];
}): JSX.Element {
  if (points.length === 0) {
    return (
      <div className="border-border/50 text-muted-foreground flex h-[180px] items-center justify-center rounded-md border border-dashed text-sm">
        No equity data yet.
      </div>
    );
  }

  let peak = -Infinity;
  const data = points.map((p) => {
    if (p.equity > peak) peak = p.equity;
    const dd = peak > 0 ? ((p.equity - peak) / peak) * 100 : 0;
    return { date: p.date, drawdown: dd };
  });
  const minDd = data.reduce((acc, d) => Math.min(acc, d.drawdown), 0);

  return (
    <div className="h-[180px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="ddGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f87171" stopOpacity={0.05} />
              <stop offset="100%" stopColor="#f87171" stopOpacity={0.5} />
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
            domain={[Math.floor(minDd * 1.1) || -1, 0]}
            width={48}
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
            }}
            labelStyle={{ color: "hsl(var(--muted-foreground))", marginBottom: 4 }}
            labelFormatter={(label: string) => formatDate(label)}
            formatter={(value: number | string) =>
              typeof value === "number" ? `${value.toFixed(2)}%` : value
            }
          />
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke="#f87171"
            strokeWidth={1.25}
            fill="url(#ddGradient)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
