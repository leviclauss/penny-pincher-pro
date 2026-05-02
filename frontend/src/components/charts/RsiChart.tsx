import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartBar } from "@/api/types";

export function RsiChart({ bars }: { bars: ChartBar[] }): JSX.Element {
  const data = bars.map((b) => ({ date: b.date, rsi_14: b.rsi_14 }));
  return (
    <div className="h-[140px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={48} />
          <YAxis tick={{ fontSize: 11 }} domain={[0, 100]} width={60} ticks={[0, 30, 50, 70, 100]} />
          <Tooltip
            contentStyle={{
              backgroundColor: "hsl(var(--card))",
              borderColor: "hsl(var(--border))",
              fontSize: 12,
            }}
            formatter={(value: number | string) =>
              typeof value === "number" ? value.toFixed(1) : value
            }
          />
          <ReferenceLine y={70} stroke="#f97316" strokeDasharray="2 2" />
          <ReferenceLine y={30} stroke="#10b981" strokeDasharray="2 2" />
          <Line
            type="monotone"
            dataKey="rsi_14"
            stroke="#6366f1"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
            name="RSI(14)"
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
