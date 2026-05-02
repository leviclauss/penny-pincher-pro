import { useEffect, useMemo, useRef, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Eye,
  EyeOff,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import {
  createTicker,
  deleteTicker,
  fetchJobRuns,
  fetchTickers,
  patchTicker,
} from "@/api/client";
import type { JobRunOut, TickerCreate, TickerSummary } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Checkbox } from "@/components/ui/Checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
import { cn } from "@/lib/utils";
import { formatDate, formatNumber, formatPercent, pctDistance } from "@/lib/format";

type SortKey =
  | "symbol"
  | "name"
  | "tier"
  | "sector"
  | "last_close"
  | "distance_200"
  | "rsi_14"
  | "iv_atm"
  | "next_earnings_date";
type SortDir = "asc" | "desc";

interface Column {
  key: SortKey;
  label: string;
  align?: "left" | "right";
}

const COLUMNS: Column[] = [
  { key: "symbol", label: "Symbol" },
  { key: "name", label: "Name" },
  { key: "tier", label: "Tier", align: "right" },
  { key: "sector", label: "Sector" },
  { key: "last_close", label: "Last", align: "right" },
  { key: "distance_200", label: "Δ 200 EMA", align: "right" },
  { key: "rsi_14", label: "RSI", align: "right" },
  { key: "iv_atm", label: "IV ATM", align: "right" },
  { key: "next_earnings_date", label: "Next ER", align: "right" },
];

function getSortValue(t: TickerSummary, key: SortKey): string | number | null {
  switch (key) {
    case "symbol":
      return t.symbol;
    case "name":
      return t.name;
    case "tier":
      return t.tier;
    case "sector":
      return t.sector;
    case "last_close":
      return t.last_close;
    case "distance_200":
      return pctDistance(t.last_close, t.ema_200);
    case "rsi_14":
      return t.rsi_14;
    case "iv_atm":
      return t.iv_atm;
    case "next_earnings_date":
      return t.next_earnings_date;
  }
}

function compareValues(a: string | number | null, b: string | number | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

function rsiTone(value: number | null): string {
  if (value === null) return "text-muted-foreground";
  if (value >= 70) return "text-amber-300";
  if (value <= 30) return "text-emerald-300";
  return "text-foreground";
}

const TIER_PALETTE: Record<number, string> = {
  1: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  2: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  3: "bg-violet-500/15 text-violet-300 ring-violet-500/30",
};

function tierSelectClasses(tier: number | null): string {
  const cls = tier === null ? "bg-muted text-muted-foreground ring-border" : TIER_PALETTE[tier];
  return cn(
    "cursor-pointer appearance-none rounded-full px-2 py-0.5 text-center text-[10px] font-semibold uppercase tracking-wider ring-1 focus-visible:outline-none focus-visible:ring-2",
    cls,
  );
}

interface TierSelectProps {
  tier: number | null;
  onChange: (tier: number | null) => void;
}

function TierSelect({ tier, onChange }: TierSelectProps): JSX.Element {
  return (
    <select
      value={tier ?? ""}
      title="Edit tier"
      className={tierSelectClasses(tier)}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => {
        const v = e.target.value;
        onChange(v === "" ? null : Number(v));
      }}
    >
      <option value="">—</option>
      <option value="1">T1</option>
      <option value="2">T2</option>
      <option value="3">T3</option>
    </select>
  );
}

export function Tickers(): JSX.Element {
  const [sortKey, setSortKey] = useState<SortKey>("symbol");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [showHidden, setShowHidden] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<TickerSummary | null>(null);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["tickers", { includeHidden: showHidden }],
    queryFn: () => fetchTickers(showHidden),
  });

  const anyRunning = useBackfillPolling();

  const invalidateTickers = (): void => {
    void qc.invalidateQueries({ queryKey: ["tickers"] });
  };

  const addMutation = useMutation({ mutationFn: createTicker, onSuccess: invalidateTickers });
  const deleteMutation = useMutation({
    mutationFn: deleteTicker,
    onSuccess: invalidateTickers,
  });
  const hideMutation = useMutation({
    mutationFn: ({ symbol, hidden }: { symbol: string; hidden: boolean }) =>
      patchTicker(symbol, { is_hidden: hidden }),
    onSuccess: invalidateTickers,
  });
  const tierMutation = useMutation({
    mutationFn: ({ symbol, tier }: { symbol: string; tier: number | null }) =>
      patchTicker(symbol, { tier }),
    onSuccess: invalidateTickers,
  });

  const sorted = useMemo(() => {
    if (!data) return [];
    const out = [...data];
    out.sort((a, b) => {
      const cmp = compareValues(getSortValue(a, sortKey), getSortValue(b, sortKey));
      return sortDir === "asc" ? cmp : -cmp;
    });
    return out;
  }, [data, sortKey, sortDir]);

  const toggleSort = (key: SortKey): void => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  return (
    <div className="space-y-6">
      <header className="space-y-1.5">
        <p className="text-primary text-xs font-semibold uppercase tracking-widest">
          Watchlist
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">Tickers</h1>
        <p className="text-muted-foreground max-w-2xl text-sm">
          Latest close, EMA distance, RSI, IV, and the next earnings event for
          every symbol on your list.
        </p>
      </header>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <CardTitle>All symbols</CardTitle>
              {anyRunning && (
                <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Backfilling…
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              <Checkbox
                label="Show hidden"
                checked={showHidden}
                onChange={(e) => setShowHidden(e.target.checked)}
              />
              <span className="text-muted-foreground text-xs">
                {data ? `${data.length} symbols` : "—"}
              </span>
              <Button size="sm" onClick={() => setAddOpen(true)}>
                <Plus className="mr-1 h-4 w-4" />
                Add ticker
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading && <div className="text-muted-foreground text-sm">Loading…</div>}
          {isError && (
            <div className="text-destructive text-sm">Failed to load tickers.</div>
          )}
          {data && data.length === 0 && (
            <div className="text-muted-foreground text-sm">
              Watchlist is empty. Click <strong>Add ticker</strong> to add your
              first symbol.
            </div>
          )}
          {data && data.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  {COLUMNS.map((col) => {
                    const active = sortKey === col.key;
                    return (
                      <TableHead
                        key={col.key}
                        className={cn(
                          "hover:text-foreground cursor-pointer select-none transition-colors",
                          col.align === "right" && "text-right",
                          active && "text-foreground",
                        )}
                        onClick={() => toggleSort(col.key)}
                      >
                        <span
                          className={cn(
                            "inline-flex items-center gap-1.5",
                            col.align === "right" && "justify-end",
                          )}
                        >
                          {col.label}
                          {active ? (
                            sortDir === "asc" ? (
                              <ArrowUp className="text-primary h-3 w-3" />
                            ) : (
                              <ArrowDown className="text-primary h-3 w-3" />
                            )
                          ) : (
                            <ArrowUpDown className="h-3 w-3 opacity-40" />
                          )}
                        </span>
                      </TableHead>
                    );
                  })}
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((t) => {
                  const distance = pctDistance(t.last_close, t.ema_200);
                  return (
                    <TableRow
                      key={t.symbol}
                      onClick={() => navigate(`/tickers/${t.symbol}`)}
                      className={cn("cursor-pointer", t.is_hidden && "opacity-60")}
                    >
                      <TableCell className="font-semibold tracking-tight">
                        {t.symbol}
                      </TableCell>
                      <TableCell className="text-muted-foreground max-w-[180px] truncate">
                        {t.name ?? "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        <TierSelect
                          tier={t.tier}
                          onChange={(tier) => tierMutation.mutate({ symbol: t.symbol, tier })}
                        />
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {t.sector ?? "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {formatNumber(t.last_close)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono",
                          distance !== null && distance >= 0 && "text-emerald-300",
                          distance !== null && distance < 0 && "text-red-300",
                        )}
                      >
                        {formatPercent(distance)}
                      </TableCell>
                      <TableCell
                        className={cn("text-right font-mono", rsiTone(t.rsi_14))}
                      >
                        {formatNumber(t.rsi_14, 1)}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {t.iv_atm === null ? "—" : `${(t.iv_atm * 100).toFixed(1)}%`}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-right font-mono text-xs">
                        {formatDate(t.next_earnings_date)}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            title={t.is_hidden ? "Unhide" : "Hide"}
                            onClick={(e) => {
                              e.stopPropagation();
                              hideMutation.mutate({
                                symbol: t.symbol,
                                hidden: !t.is_hidden,
                              });
                            }}
                          >
                            {t.is_hidden ? (
                              <Eye className="h-4 w-4" />
                            ) : (
                              <EyeOff className="h-4 w-4" />
                            )}
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            title="Delete"
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfirmDelete(t);
                            }}
                          >
                            <Trash2 className="text-destructive h-4 w-4" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <AddTickerDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onAdded={() => setAddOpen(false)}
        addMutation={addMutation}
      />

      <DeleteTickerDialog
        target={confirmDelete}
        onClose={() => setConfirmDelete(null)}
        onHide={(symbol) => {
          hideMutation.mutate({ symbol, hidden: true });
          setConfirmDelete(null);
        }}
        onDelete={(symbol) =>
          deleteMutation.mutateAsync(symbol).then(() => setConfirmDelete(null))
        }
        pendingDelete={deleteMutation.isPending}
      />
    </div>
  );
}

function useBackfillPolling(): boolean {
  const qc = useQueryClient();
  const lastRunIdsRef = useRef<Set<number>>(new Set());
  const [polling, setPolling] = useState(false);

  const { data } = useQuery({
    queryKey: ["job-runs", "ticker_backfill"],
    queryFn: () => fetchJobRuns("ticker_backfill", 5),
    refetchInterval: polling ? 4000 : 30000,
  });

  useEffect(() => {
    if (!data) return;
    const running = data.some((r: JobRunOut) => r.status === "running");
    setPolling(running);

    // Detect job completions we haven't seen yet → invalidate the table.
    const seen = lastRunIdsRef.current;
    const completedNew = data.filter(
      (r) => (r.status === "success" || r.status === "failure") && !seen.has(r.id),
    );
    if (completedNew.length > 0) {
      void qc.invalidateQueries({ queryKey: ["tickers"] });
    }
    lastRunIdsRef.current = new Set(data.map((r) => r.id));
  }, [data, qc]);

  return polling;
}

interface AddTickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded: () => void;
  addMutation: UseMutationResult<TickerSummary, Error, TickerCreate>;
}

function AddTickerDialog({
  open,
  onOpenChange,
  onAdded,
  addMutation,
}: AddTickerDialogProps): JSX.Element {
  const [symbol, setSymbol] = useState("");
  const [name, setName] = useState("");
  const [tier, setTier] = useState<string>("");

  const { mutateAsync, reset, isPending, error } = addMutation;
  const errorMessage = error?.message ?? null;

  useEffect(() => {
    if (!open) {
      setSymbol("");
      setName("");
      setTier("");
      reset();
    }
  }, [open, reset]);

  const handleSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    if (!symbol.trim()) return;
    void mutateAsync({
      symbol: symbol.trim().toUpperCase(),
      name: name.trim() || null,
      tier: tier ? Number(tier) : null,
    }).then(onAdded);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Add ticker</DialogTitle>
            <DialogDescription>
              We'll backfill ~5 years of bars, indicators, options/IV, and earnings
              for this symbol. Takes 30–60 seconds in the background.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-foreground text-xs font-medium" htmlFor="add-symbol">
                Symbol *
              </label>
              <Input
                id="add-symbol"
                value={symbol}
                autoFocus
                placeholder="e.g. NVDA"
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                maxLength={16}
                required
              />
            </div>
            <div className="space-y-1">
              <label className="text-foreground text-xs font-medium" htmlFor="add-name">
                Name (optional)
              </label>
              <Input
                id="add-name"
                value={name}
                placeholder="Auto-filled from Finnhub if blank"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <label className="text-foreground text-xs font-medium" htmlFor="add-tier">
                Tier (optional)
              </label>
              <select
                id="add-tier"
                value={tier}
                onChange={(e) => setTier(e.target.value)}
                className="border-border bg-background text-foreground focus-visible:ring-ring h-9 w-full rounded-md border px-3 text-sm focus-visible:outline-none focus-visible:ring-2"
              >
                <option value="">— None —</option>
                <option value="1">T1 (major)</option>
                <option value="2">T2 (secondary)</option>
                <option value="3">T3 (watchlist)</option>
              </select>
            </div>
            {errorMessage && (
              <div className="text-destructive text-xs">{errorMessage}</div>
            )}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isPending || !symbol.trim()}>
              {isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Adding…
                </>
              ) : (
                "Add ticker"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface DeleteTickerDialogProps {
  target: TickerSummary | null;
  onClose: () => void;
  onHide: (symbol: string) => void;
  onDelete: (symbol: string) => Promise<void>;
  pendingDelete: boolean;
}

function DeleteTickerDialog({
  target,
  onClose,
  onHide,
  onDelete,
  pendingDelete,
}: DeleteTickerDialogProps): JSX.Element {
  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {target?.symbol}?</DialogTitle>
          <DialogDescription>
            This permanently deletes the ticker AND its IV / indicator / options /
            earnings history. <strong>IV history cannot be recovered</strong> —
            Alpaca's options history is shallow, so re-adding won't bring it back.
            <br />
            <br />
            If you only want to remove it from this table while keeping ingestion
            running, hide it instead.
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={pendingDelete}>
            Cancel
          </Button>
          <Button
            variant="secondary"
            onClick={() => target && onHide(target.symbol)}
            disabled={pendingDelete}
          >
            Hide instead
          </Button>
          <Button
            variant="destructive"
            onClick={() => target && void onDelete(target.symbol)}
            disabled={pendingDelete}
          >
            {pendingDelete ? (
              <>
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                Deleting…
              </>
            ) : (
              "Delete permanently"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
