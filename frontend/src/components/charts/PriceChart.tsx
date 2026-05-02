import { useMemo, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartBar, UpcomingEarning } from "@/api/types";
import { cn } from "@/lib/utils";

interface Props {
  bars: ChartBar[];
  earnings?: UpcomingEarning[];
}

type EmaKey = "ema_20" | "ema_50" | "ema_200";

const EMA_META: Record<EmaKey, { label: string; stroke: string }> = {
  ema_20: { label: "EMA 20", stroke: "#60a5fa" },
  ema_50: { label: "EMA 50", stroke: "#c084fc" },
  ema_200: { label: "EMA 200", stroke: "#fbbf24" },
};

const AXIS_COLOR = "hsl(240 5% 50%)";
const GRID_COLOR = "hsl(240 5% 16% / 0.7)";

export function PriceChart({ bars, earnings = [] }: Props): JSX.Element {
  const [enabled, setEnabled] = useState<Record<EmaKey, boolean>>({
    ema_20: true,
    ema_50: true,
    ema_200: true,
  });

  const data = useMemo(
    () =>
      bars.map((b) => ({
        date: b.date,
        close: b.close,
        ema_20: b.ema_20,
        ema_50: b.ema_50,
        ema_200: b.ema_200,
      })),
    [bars],
  );

  const earningsInRange = useMemo(() => {
    if (data.length === 0) return [];
    const first = data[0]?.date;
    const last = data[data.length - 1]?.date;
    if (!first || !last) return [];
    return earnings.filter((e) => e.earnings_date >= first && e.earnings_date <= last);
  }, [data, earnings]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-[10px] font-semibold uppercase tracking-widest">
          Overlays
        </span>
        {(Object.keys(EMA_META) as EmaKey[]).map((key) => {
          const meta = EMA_META[key];
          const on = enabled[key];
          return (
            <button
              key={key}
              type="button"
              onClick={() => setEnabled((p) => ({ ...p, [key]: !p[key] }))}
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors",
                on
                  ? "border-transparent text-foreground bg-card shadow-sm"
                  : "border-border/60 text-muted-foreground hover:text-foreground",
              )}
            >
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: meta.stroke, opacity: on ? 1 : 0.4 }}
              />
              {meta.label}
            </button>
          );
        })}
      </div>
      <div className="h-[380px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
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
              domain={["auto", "auto"]}
              width={60}
              tickFormatter={(v: number) => v.toFixed(0)}
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
                typeof value === "number" ? value.toFixed(2) : value
              }
            />
            <Line
              type="monotone"
              dataKey="close"
              stroke="hsl(0 0% 96%)"
              dot={false}
              strokeWidth={1.75}
              isAnimationActive={false}
              name="Close"
            />
            {(Object.keys(EMA_META) as EmaKey[]).map((key) =>
              enabled[key] ? (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={EMA_META[key].stroke}
                  dot={false}
                  strokeWidth={1.25}
                  isAnimationActive={false}
                  name={EMA_META[key].label}
                  connectNulls
                />
              ) : null,
            )}
            {earningsInRange.map((e) => (
              <ReferenceLine
                key={e.earnings_date}
                x={e.earnings_date}
                stroke="hsl(0 72% 60%)"
                strokeDasharray="4 2"
                strokeOpacity={0.7}
                label={{
                  value: "ER",
                  position: "top",
                  fontSize: 10,
                  fill: "hsl(0 72% 70%)",
                }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
