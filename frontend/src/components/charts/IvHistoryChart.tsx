import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { IVPoint } from "@/api/types";

export function IvHistoryChart({ points }: { points: IVPoint[] }): JSX.Element {
  const data = points
    .filter((p) => p.iv_atm !== null)
    .map((p) => ({ date: p.date, iv_atm: p.iv_atm === null ? null : p.iv_atm * 100 }));

  if (data.length === 0) {
    return (
      <div className="text-muted-foreground py-12 text-center text-sm">
        No IV history yet. Options ingestion needs to run for at least one day.
      </div>
    );
  }

  return (
    <div className="h-[220px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={48} />
          <YAxis
            tick={{ fontSize: 11 }}
            width={60}
            tickFormatter={(v: number) => `${v.toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "hsl(var(--card))",
              borderColor: "hsl(var(--border))",
              fontSize: 12,
            }}
            formatter={(value: number | string) =>
              typeof value === "number" ? `${value.toFixed(1)}%` : value
            }
          />
          <Line
            type="monotone"
            dataKey="iv_atm"
            stroke="#0ea5e9"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
            name="IV ATM"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
