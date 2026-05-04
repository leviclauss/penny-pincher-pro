import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BacktestEquityPoint } from "@/api/types";
import { formatDate, formatDateShort } from "@/lib/format";

const AXIS_COLOR = "hsl(240 5% 50%)";
const GRID_COLOR = "hsl(240 5% 16% / 0.7)";
const SPY_COLOR = "#94a3b8";

export function EquityChart({
  points,
  startingCapital,
}: {
  points: BacktestEquityPoint[];
  startingCapital: number | null;
}): JSX.Element {
  if (points.length === 0) {
    return (
      <div className="border-border/50 text-muted-foreground flex h-[260px] items-center justify-center rounded-md border border-dashed text-sm">
        No equity data yet — run a strategy backtest first.
      </div>
    );
  }

  const hasSpy = points.some((p) => p.spy_benchmark != null);
  const data = points.map((p) => ({
    date: p.date,
    equity: p.equity,
    spy: p.spy_benchmark ?? undefined,
  }));

  return (
    <div className="h-[260px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#34d399" stopOpacity={0.4} />
              <stop offset="100%" stopColor="#34d399" stopOpacity={0.02} />
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
            domain={["auto", "auto"]}
            width={70}
            tickFormatter={(v: number) => `$${Math.round(v).toLocaleString()}`}
          />
          {startingCapital != null && (
            <ReferenceLine
              y={startingCapital}
              stroke={AXIS_COLOR}
              strokeDasharray="3 3"
              label={{
                value: "starting capital",
                position: "insideTopLeft",
                fill: AXIS_COLOR,
                fontSize: 10,
              }}
            />
          )}
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
              typeof value === "number"
                ? `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                : value
            }
          />
          {hasSpy && (
            <Legend
              verticalAlign="top"
              align="right"
              iconType="line"
              iconSize={12}
              wrapperStyle={{ fontSize: 11, paddingBottom: 4 }}
            />
          )}
          <Area
            type="monotone"
            dataKey="equity"
            stroke="#34d399"
            strokeWidth={1.75}
            fill="url(#equityGradient)"
            isAnimationActive={false}
            name="Strategy"
          />
          {hasSpy && (
            <Line
              type="monotone"
              dataKey="spy"
              stroke={SPY_COLOR}
              strokeWidth={1.25}
              strokeDasharray="4 3"
              dot={false}
              isAnimationActive={false}
              name="SPY (buy & hold)"
              connectNulls
            />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
