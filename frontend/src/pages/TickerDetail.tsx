import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import {
  fetchTickerChart,
  fetchTickerIvHistory,
  fetchTickers,
  fetchUpcomingEarnings,
} from "@/api/client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { PriceChart } from "@/components/charts/PriceChart";
import { RsiChart } from "@/components/charts/RsiChart";
import { IvHistoryChart } from "@/components/charts/IvHistoryChart";
import { cn } from "@/lib/utils";
import { formatNumber, formatPercent } from "@/lib/format";

export function TickerDetail(): JSX.Element {
  const { symbol } = useParams<{ symbol: string }>();
  const sym = symbol?.toUpperCase() ?? "";

  const tickers = useQuery({ queryKey: ["tickers"], queryFn: fetchTickers });
  const chart = useQuery({
    queryKey: ["ticker", sym, "chart", "1y"],
    queryFn: () => fetchTickerChart(sym, "1y"),
    enabled: sym.length > 0,
  });
  const iv = useQuery({
    queryKey: ["ticker", sym, "iv", "1y"],
    queryFn: () => fetchTickerIvHistory(sym, "1y"),
    enabled: sym.length > 0,
  });
  // Pull up to 90 days of upcoming earnings so any markers in the 1y window land.
  const earnings = useQuery({
    queryKey: ["earnings", "upcoming", 90],
    queryFn: () => fetchUpcomingEarnings(90),
  });

  const ticker = tickers.data?.find((t) => t.symbol === sym);

  const dayChangePct = useMemo(() => {
    if (!chart.data || chart.data.length < 2) return null;
    const last = chart.data[chart.data.length - 1];
    const prev = chart.data[chart.data.length - 2];
    if (!last || !prev || prev.close === 0) return null;
    return ((last.close - prev.close) / prev.close) * 100;
  }, [chart.data]);

  if (!sym) {
    return <div className="text-muted-foreground">No symbol provided.</div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/tickers"
          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
        >
          <ChevronLeft className="h-4 w-4" /> Tickers
        </Link>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-3">
          <h1 className="text-3xl font-semibold">{sym}</h1>
          <span className="text-muted-foreground text-base">{ticker?.name ?? "—"}</span>
          {ticker?.tier !== null && ticker?.tier !== undefined && (
            <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-xs">
              Tier {ticker.tier}
            </span>
          )}
        </div>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="text-2xl font-semibold">
            {ticker?.last_close !== null && ticker?.last_close !== undefined
              ? formatNumber(ticker.last_close)
              : "—"}
          </span>
          {dayChangePct !== null && (
            <span
              className={cn(
                "text-sm font-medium",
                dayChangePct >= 0 ? "text-emerald-600" : "text-red-600",
              )}
            >
              {dayChangePct >= 0 ? "+" : ""}
              {formatPercent(dayChangePct)}
            </span>
          )}
          {ticker?.last_close_date && (
            <span className="text-muted-foreground font-mono text-xs">
              as of {ticker.last_close_date}
            </span>
          )}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Price (1y)</CardTitle>
          <p className="text-muted-foreground text-xs">
            Daily close with EMA overlays. Toggle the buttons to hide overlays. Dashed
            red lines mark upcoming earnings.
          </p>
        </CardHeader>
        <CardContent>
          {chart.isLoading && (
            <div className="text-muted-foreground text-sm">Loading…</div>
          )}
          {chart.isError && (
            <div className="text-destructive text-sm">Failed to load chart.</div>
          )}
          {chart.data && chart.data.length === 0 && (
            <div className="text-muted-foreground text-sm">
              No bars yet for this symbol.
            </div>
          )}
          {chart.data && chart.data.length > 0 && (
            <>
              <PriceChart
                bars={chart.data}
                earnings={earnings.data?.filter((e) => e.symbol === sym) ?? []}
              />
              <div className="mt-4">
                <h3 className="text-muted-foreground mb-2 text-xs font-medium uppercase">
                  RSI (14)
                </h3>
                <RsiChart bars={chart.data} />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Implied volatility (1y)</CardTitle>
          <p className="text-muted-foreground text-xs">
            ATM IV for the front-month chain.
          </p>
        </CardHeader>
        <CardContent>
          {iv.isLoading && <div className="text-muted-foreground text-sm">Loading…</div>}
          {iv.isError && (
            <div className="text-destructive text-sm">Failed to load IV history.</div>
          )}
          {iv.data && <IvHistoryChart points={iv.data} />}
        </CardContent>
      </Card>
    </div>
  );
}
