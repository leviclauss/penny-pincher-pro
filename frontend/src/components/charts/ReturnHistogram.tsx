import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const AXIS_COLOR = "hsl(240 5% 50%)";
const GRID_COLOR = "hsl(240 5% 16% / 0.7)";
const POS_COLOR = "#34d399";
const NEG_COLOR = "#f87171";

const DEFAULT_BIN_COUNT = 18;

export function ReturnHistogram({
  values,
}: {
  /** Per-trade realized P&L in dollars. Open trades should be excluded by the caller. */
  values: number[];
}): JSX.Element {
  if (values.length === 0) {
    return (
      <div className="border-border/50 text-muted-foreground flex h-[180px] items-center justify-center rounded-md border border-dashed text-sm">
        No closed trades to plot.
      </div>
    );
  }

  const bins = computeBins(values, DEFAULT_BIN_COUNT);

  return (
    <div className="h-[180px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={bins} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: AXIS_COLOR }}
            axisLine={{ stroke: GRID_COLOR }}
            tickLine={{ stroke: GRID_COLOR }}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 11, fill: AXIS_COLOR }}
            axisLine={{ stroke: GRID_COLOR }}
            tickLine={{ stroke: GRID_COLOR }}
            allowDecimals={false}
            width={32}
          />
          <Tooltip
            cursor={{ fill: "hsl(var(--muted) / 0.4)" }}
            contentStyle={{
              backgroundColor: "hsl(240 6% 9% / 0.95)",
              border: "1px solid hsl(var(--border))",
              borderRadius: 8,
              fontSize: 12,
              color: "hsl(var(--foreground))",
            }}
            labelFormatter={(label: string) => `Range: ${label}`}
            formatter={(value: number | string) =>
              typeof value === "number"
                ? `${value} trade${value === 1 ? "" : "s"}`
                : value
            }
          />
          <Bar dataKey="count" isAnimationActive={false}>
            {bins.map((bin) => (
              <Cell key={bin.label} fill={bin.center >= 0 ? POS_COLOR : NEG_COLOR} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

interface Bin {
  label: string;
  count: number;
  center: number;
}

function computeBins(values: number[], binCount: number): Bin[] {
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (lo === hi) {
    // All identical → fan it out a touch so the histogram has visible width.
    lo -= 1;
    hi += 1;
  }
  const width = (hi - lo) / binCount;
  const bins: Bin[] = [];
  for (let i = 0; i < binCount; i++) {
    const start = lo + i * width;
    const end = start + width;
    const center = (start + end) / 2;
    bins.push({
      label: `${formatBound(start)}…${formatBound(end)}`,
      count: 0,
      center,
    });
  }
  for (const v of values) {
    let idx = Math.floor((v - lo) / width);
    if (idx >= binCount) idx = binCount - 1; // hi falls into the last bin
    if (idx < 0) idx = 0;
    bins[idx].count += 1;
  }
  return bins;
}

function formatBound(v: number): string {
  if (Math.abs(v) >= 1000) {
    return `${(v / 1000).toFixed(1)}k`;
  }
  return v.toFixed(0);
}
