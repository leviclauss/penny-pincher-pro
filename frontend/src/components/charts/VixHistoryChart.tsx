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
import type { MacroPoint } from "@/api/types";
import { formatDate, formatDateShort } from "@/lib/format";

const AXIS_COLOR = "hsl(240 5% 50%)";
const GRID_COLOR = "hsl(240 5% 16% / 0.7)";

export function VixHistoryChart({ points }: { points: MacroPoint[] }): JSX.Element {
  const data = points
    .filter((p) => p.vix_close !== null || p.vix_9d !== null)
    .map((p) => ({
      date: p.date,
      vix_close: p.vix_close,
      vix_9d: p.vix_9d,
    }));

  if (data.length === 0) {
    return (
      <div className="border-border/50 text-muted-foreground flex h-[260px] items-center justify-center rounded-md border border-dashed text-sm">
        No VIX history yet — run macro ingestion to populate.
      </div>
    );
  }

  return (
    <div className="h-[260px] w-full">
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
            width={40}
            tickFormatter={(v: number) => v.toFixed(0)}
          />
          <ReferenceLine
            y={20}
            stroke="hsl(45 90% 55% / 0.45)"
            strokeDasharray="4 4"
            label={{
              value: "20",
              position: "right",
              fill: "hsl(45 90% 55% / 0.7)",
              fontSize: 10,
            }}
          />
          <ReferenceLine
            y={30}
            stroke="hsl(0 75% 60% / 0.45)"
            strokeDasharray="4 4"
            label={{
              value: "30",
              position: "right",
              fill: "hsl(0 75% 60% / 0.7)",
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
              typeof value === "number" ? value.toFixed(2) : value
            }
          />
          <Legend
            wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
            iconType="plainline"
          />
          <Line
            type="monotone"
            dataKey="vix_close"
            name="VIX"
            stroke="#f97316"
            strokeWidth={1.75}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="vix_9d"
            name="VIX9D"
            stroke="#a78bfa"
            strokeWidth={1.25}
            strokeDasharray="4 3"
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
