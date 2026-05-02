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
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

interface Props {
  bars: ChartBar[];
  earnings?: UpcomingEarning[];
}

type EmaKey = "ema_20" | "ema_50" | "ema_200";

const EMA_META: Record<EmaKey, { label: string; stroke: string }> = {
  ema_20: { label: "EMA 20", stroke: "#2563eb" },
  ema_50: { label: "EMA 50", stroke: "#9333ea" },
  ema_200: { label: "EMA 200", stroke: "#f97316" },
};

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
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {(Object.keys(EMA_META) as EmaKey[]).map((key) => (
          <Button
            key={key}
            type="button"
            size="sm"
            variant={enabled[key] ? "default" : "outline"}
            onClick={() => setEnabled((p) => ({ ...p, [key]: !p[key] }))}
            className={cn("h-7 text-xs")}
            style={enabled[key] ? { backgroundColor: EMA_META[key].stroke } : undefined}
          >
            {EMA_META[key].label}
          </Button>
        ))}
      </div>
      <div className="h-[360px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
            <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={48} />
            <YAxis
              tick={{ fontSize: 11 }}
              domain={["auto", "auto"]}
              width={60}
              tickFormatter={(v: number) => v.toFixed(0)}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "hsl(var(--card))",
                borderColor: "hsl(var(--border))",
                fontSize: 12,
              }}
              formatter={(value: number | string) =>
                typeof value === "number" ? value.toFixed(2) : value
              }
            />
            <Line
              type="monotone"
              dataKey="close"
              stroke="hsl(var(--foreground))"
              dot={false}
              strokeWidth={1.5}
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
                stroke="hsl(var(--destructive))"
                strokeDasharray="4 2"
                label={{
                  value: "ER",
                  position: "top",
                  fontSize: 10,
                  fill: "hsl(var(--destructive))",
                }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
