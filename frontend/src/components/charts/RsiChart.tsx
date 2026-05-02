import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartBar } from "@/api/types";

const AXIS_COLOR = "hsl(240 5% 50%)";
const GRID_COLOR = "hsl(240 5% 16% / 0.7)";

export function RsiChart({ bars }: { bars: ChartBar[] }): JSX.Element {
  const data = bars.map((b) => ({ date: b.date, rsi_14: b.rsi_14 }));
  return (
    <div className="h-[150px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            axisLine={{ stroke: GRID_COLOR }}
            tickLine={{ stroke: GRID_COLOR }}
            minTickGap={48}
          />
          <YAxis
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            axisLine={{ stroke: GRID_COLOR }}
            tickLine={{ stroke: GRID_COLOR }}
            domain={[0, 100]}
            width={60}
            ticks={[0, 30, 50, 70, 100]}
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
            formatter={(value: number | string) =>
              typeof value === "number" ? value.toFixed(1) : value
            }
          />
          <ReferenceArea y1={70} y2={100} fill="hsl(38 92% 55%)" fillOpacity={0.06} />
          <ReferenceArea y1={0} y2={30} fill="hsl(152 65% 45%)" fillOpacity={0.06} />
          <ReferenceLine y={70} stroke="hsl(38 92% 55%)" strokeDasharray="2 2" strokeOpacity={0.6} />
          <ReferenceLine y={30} stroke="hsl(152 65% 45%)" strokeDasharray="2 2" strokeOpacity={0.6} />
          <Line
            type="monotone"
            dataKey="rsi_14"
            stroke="#a5b4fc"
            strokeWidth={1.75}
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
