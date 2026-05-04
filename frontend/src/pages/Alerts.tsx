import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Eye } from "lucide-react";
import { ackAlert, fetchAlertTypes, fetchAlerts } from "@/api/client";
import type { AlertOut } from "@/api/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/Table";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

interface AlertActionsProps {
  alert: AlertOut;
  onView: (alert: AlertOut) => void;
}

function AlertActions({ alert, onView }: AlertActionsProps): JSX.Element {
  const qc = useQueryClient();
  const ack = useMutation({
    mutationFn: () => ackAlert(alert.id, !alert.user_acked),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
  return (
    <div className="flex justify-end gap-1.5">
      <Button
        size="sm"
        variant="outline"
        onClick={() => onView(alert)}
        title="View payload"
      >
        <Eye className="h-3 w-3" />
      </Button>
      <Button
        size="sm"
        variant={alert.user_acked ? "outline" : "default"}
        onClick={() => ack.mutate()}
        disabled={ack.isPending}
        title={alert.user_acked ? "Mark as unacked" : "Mark as acked"}
      >
        <Check className="h-3 w-3" />
        <span className="ml-1.5">{alert.user_acked ? "Unack" : "Ack"}</span>
      </Button>
    </div>
  );
}

function AlertMobileCard({
  alert,
  onView,
}: {
  alert: AlertOut;
  onView: (alert: AlertOut) => void;
}): JSX.Element {
  return (
    <li
      onClick={() => onView(alert)}
      className={cn(
        "active:bg-accent/40 cursor-pointer px-1 py-3 transition-colors",
        alert.user_acked && "opacity-60",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="default">{alert.alert_type}</Badge>
            {alert.symbol && (
              <span className="font-mono text-xs font-semibold">{alert.symbol}</span>
            )}
            {alert.user_acked ? (
              <Badge variant="success">acked</Badge>
            ) : (
              <Badge variant="warning">new</Badge>
            )}
          </div>
          <div className="text-muted-foreground mt-1 font-mono text-[11px]">
            {formatDateTime(alert.triggered_at)}
          </div>
        </div>
      </div>
      <div className="text-muted-foreground mt-2 line-clamp-2 text-xs">
        {summarizePayload(alert.payload)}
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-muted-foreground font-mono text-[11px]">
          {alert.channels_sent.length > 0 ? alert.channels_sent.join(", ") : "no channels"}
        </span>
        <div onClick={(e) => e.stopPropagation()}>
          <AlertActions alert={alert} onView={onView} />
        </div>
      </div>
    </li>
  );
}

const PAGE_SIZE = 100;

interface Filters {
  alertType: string;
  symbol: string;
  since: string;
  until: string;
}

const EMPTY_FILTERS: Filters = {
  alertType: "",
  symbol: "",
  since: "",
  until: "",
};

function summarizePayload(payload: Record<string, unknown>): string {
  const keys = Object.keys(payload);
  if (keys.length === 0) return "—";
  const previewKeys = keys.slice(0, 3);
  return previewKeys
    .map((k) => {
      const v = payload[k];
      const text =
        typeof v === "string" || typeof v === "number" || typeof v === "boolean"
          ? String(v)
          : Array.isArray(v)
            ? `[${v.length}]`
            : "{…}";
      return `${k}=${text}`;
    })
    .join(" · ");
}

function PayloadDialog({
  alert,
  open,
  onOpenChange,
}: {
  alert: AlertOut | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}): JSX.Element {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            {alert ? `${alert.alert_type}` : "—"}
            {alert?.symbol ? (
              <span className="text-muted-foreground ml-2 font-mono text-sm">
                {alert.symbol}
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            {alert ? formatDateTime(alert.triggered_at) : ""}
          </DialogDescription>
        </DialogHeader>
        {alert ? (
          <div className="space-y-3">
            <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
              <span>
                channels:{" "}
                <span className="text-foreground font-mono">
                  {alert.channels_sent.length > 0
                    ? alert.channels_sent.join(", ")
                    : "none"}
                </span>
              </span>
              <span>
                acked:{" "}
                <span className="text-foreground font-mono">
                  {alert.user_acked ? "yes" : "no"}
                </span>
              </span>
              {alert.config_id !== null ? (
                <span>
                  config_id:{" "}
                  <span className="text-foreground font-mono">{alert.config_id}</span>
                </span>
              ) : null}
            </div>
            <pre className="bg-muted/40 max-h-[60vh] overflow-auto rounded-md p-3 font-mono text-xs">
              {JSON.stringify(alert.payload, null, 2)}
            </pre>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function AlertRow({
  alert,
  onView,
}: {
  alert: AlertOut;
  onView: (alert: AlertOut) => void;
}): JSX.Element {
  return (
    <TableRow
      className={cn("cursor-pointer", alert.user_acked && "opacity-60")}
      onClick={() => onView(alert)}
    >
      <TableCell className="font-mono text-xs">
        {formatDateTime(alert.triggered_at)}
      </TableCell>
      <TableCell>
        <Badge variant="default">{alert.alert_type}</Badge>
      </TableCell>
      <TableCell className="font-mono text-xs">
        {alert.symbol ?? <span className="text-muted-foreground">—</span>}
      </TableCell>
      <TableCell className="text-muted-foreground max-w-md truncate text-xs">
        {summarizePayload(alert.payload)}
      </TableCell>
      <TableCell className="font-mono text-[11px]">
        {alert.channels_sent.length > 0 ? (
          alert.channels_sent.join(", ")
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell>
        {alert.user_acked ? (
          <Badge variant="success">acked</Badge>
        ) : (
          <Badge variant="warning">new</Badge>
        )}
      </TableCell>
      <TableCell onClick={(e) => e.stopPropagation()}>
        <AlertActions alert={alert} onView={onView} />
      </TableCell>
    </TableRow>
  );
}

export function Alerts(): JSX.Element {
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  const [viewing, setViewing] = useState<AlertOut | null>(null);

  const { data: types } = useQuery({
    queryKey: ["alert-types"],
    queryFn: fetchAlertTypes,
    staleTime: 60_000,
  });

  const queryParams = useMemo(
    () => ({
      alertType: filters.alertType || null,
      symbol: filters.symbol ? filters.symbol.toUpperCase() : null,
      since: filters.since ? new Date(filters.since).toISOString() : null,
      until: filters.until ? new Date(filters.until).toISOString() : null,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [filters, page],
  );

  const { data, isLoading, isError, isFetching } = useQuery({
    queryKey: ["alerts", queryParams],
    queryFn: () => fetchAlerts(queryParams),
    refetchInterval: 30_000,
  });

  const apply = (): void => {
    setFilters(draft);
    setPage(0);
  };

  const reset = (): void => {
    setDraft(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
    setPage(0);
  };

  return (
    <div className="space-y-8">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          System
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Alert history</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          Every alert ever fired, newest first. Click a row to inspect the
          payload that was rendered into the Telegram message. Acknowledge to
          mark as acted-on; this is a personal toggle and doesn't affect future
          dispatches.
        </p>
      </header>

      <Card>
        <CardHeader className="px-3 sm:px-5">
          <CardTitle>Filters</CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-3 sm:px-5 sm:pb-5">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <div className="space-y-1.5">
              <label className="text-muted-foreground text-xs uppercase tracking-wider">
                Alert type
              </label>
              <select
                value={draft.alertType}
                onChange={(e) => setDraft({ ...draft, alertType: e.target.value })}
                className="border-border bg-background text-foreground focus-visible:ring-ring flex h-9 w-full rounded-md border px-3 py-1 text-sm focus-visible:outline-none focus-visible:ring-2"
              >
                <option value="">all</option>
                {(types ?? []).map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-muted-foreground text-xs uppercase tracking-wider">
                Symbol
              </label>
              <Input
                value={draft.symbol}
                onChange={(e) => setDraft({ ...draft, symbol: e.target.value })}
                placeholder="AAPL"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-muted-foreground text-xs uppercase tracking-wider">
                Since
              </label>
              <Input
                type="date"
                value={draft.since}
                onChange={(e) => setDraft({ ...draft, since: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-muted-foreground text-xs uppercase tracking-wider">
                Until
              </label>
              <Input
                type="date"
                value={draft.until}
                onChange={(e) => setDraft({ ...draft, until: e.target.value })}
              />
            </div>
            <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-1">
              <Button onClick={apply} className="flex-1 sm:flex-initial">
                Apply
              </Button>
              <Button variant="outline" onClick={reset} className="flex-1 sm:flex-initial">
                Reset
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="px-3 sm:px-5">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <CardTitle>
              Recent alerts
              {isFetching ? (
                <span className="text-muted-foreground ml-2 text-xs font-normal">
                  refreshing…
                </span>
              ) : null}
            </CardTitle>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
              >
                Prev
              </Button>
              <span className="text-muted-foreground font-mono text-xs">
                page {page + 1}
              </span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setPage((p) => p + 1)}
                disabled={!data || data.length < PAGE_SIZE}
              >
                Next
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="px-0">
          {isLoading ? (
            <div className="text-muted-foreground px-5 py-8 text-center text-sm">
              Loading…
            </div>
          ) : isError ? (
            <div className="text-destructive px-5 py-8 text-center text-sm">
              Failed to load alerts.
            </div>
          ) : !data || data.length === 0 ? (
            <div className="text-muted-foreground px-5 py-8 text-center text-sm">
              No alerts match.
            </div>
          ) : (
            <>
              <ul className="divide-border/50 mx-3 divide-y md:hidden">
                {data.map((alert) => (
                  <AlertMobileCard
                    key={alert.id}
                    alert={alert}
                    onView={setViewing}
                  />
                ))}
              </ul>
              <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Triggered</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Symbol</TableHead>
                      <TableHead>Payload</TableHead>
                      <TableHead>Channels</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right"></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.map((alert) => (
                      <AlertRow
                        key={alert.id}
                        alert={alert}
                        onView={setViewing}
                      />
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <PayloadDialog
        alert={viewing}
        open={viewing !== null}
        onOpenChange={(v) => !v && setViewing(null)}
      />
    </div>
  );
}
