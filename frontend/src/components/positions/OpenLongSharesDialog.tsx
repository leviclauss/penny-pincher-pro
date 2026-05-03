import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { openLongShares } from "@/api/client";
import type { AcquisitionSource } from "@/api/types";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { cn } from "@/lib/utils";
import { todayIso } from "@/lib/positions";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function OpenLongSharesDialog({ open, onOpenChange }: Props): JSX.Element {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [symbol, setSymbol] = useState("");
  const [shares, setShares] = useState("100");
  const [costBasis, setCostBasis] = useState("");
  const [openedOn, setOpenedOn] = useState(todayIso());
  const [acquisitionSource, setAcquisitionSource] =
    useState<AcquisitionSource | null>(null);
  const [fees, setFees] = useState("0");
  const [notes, setNotes] = useState("");

  const mutation = useMutation({
    mutationFn: openLongShares,
    onSuccess: (position) => {
      void qc.invalidateQueries({ queryKey: ["positions"] });
      onOpenChange(false);
      navigate(`/positions/${position.id}`);
    },
  });

  useEffect(() => {
    if (!open) {
      setSymbol("");
      setShares("100");
      setCostBasis("");
      setOpenedOn(todayIso());
      setAcquisitionSource(null);
      setFees("0");
      setNotes("");
      mutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const submit = (e: React.FormEvent): void => {
    e.preventDefault();
    if (!symbol.trim() || !shares || !costBasis || !openedOn || !acquisitionSource)
      return;
    mutation.mutate({
      symbol: symbol.trim().toUpperCase(),
      shares: Number(shares),
      cost_basis: Number(costBasis),
      opened_on: openedOn,
      acquisition_source: acquisitionSource,
      fees: fees ? Number(fees) : 0,
      notes: notes.trim() || null,
    });
  };

  const errorMessage = mutation.error?.message ?? null;
  const canSubmit =
    !!symbol.trim() &&
    !!shares &&
    !!costBasis &&
    !!openedOn &&
    !!acquisitionSource &&
    !mutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Open long shares</DialogTitle>
            <DialogDescription>
              Record shares you already hold so you can write covered calls
              against them later.
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Symbol *" htmlFor="ols-symbol">
              <Input
                id="ols-symbol"
                autoFocus
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="NVDA"
                maxLength={16}
                required
              />
            </Field>
            <Field label="Opened on *" htmlFor="ols-opened">
              <Input
                id="ols-opened"
                type="date"
                value={openedOn}
                onChange={(e) => setOpenedOn(e.target.value)}
                required
              />
            </Field>
            <Field label="Shares *" htmlFor="ols-shares">
              <Input
                id="ols-shares"
                type="number"
                step="1"
                min="1"
                value={shares}
                onChange={(e) => setShares(e.target.value)}
                required
              />
            </Field>
            <Field label="Cost basis / share *" htmlFor="ols-cost">
              <Input
                id="ols-cost"
                type="number"
                step="0.01"
                min="0"
                value={costBasis}
                onChange={(e) => setCostBasis(e.target.value)}
                placeholder="170.00"
                required
              />
            </Field>
            <div className="col-span-2 space-y-1">
              <label className="text-foreground text-xs font-medium">
                Acquisition source *
              </label>
              <div className="flex gap-2">
                <SourcePill
                  selected={acquisitionSource === "open_market"}
                  onClick={() => setAcquisitionSource("open_market")}
                >
                  Open market
                </SourcePill>
                <SourcePill
                  selected={acquisitionSource === "assignment"}
                  onClick={() => setAcquisitionSource("assignment")}
                >
                  Assignment
                </SourcePill>
              </div>
            </div>
            <Field label="Fees" htmlFor="ols-fees">
              <Input
                id="ols-fees"
                type="number"
                step="0.01"
                min="0"
                value={fees}
                onChange={(e) => setFees(e.target.value)}
              />
            </Field>
            <div />
            <div className="col-span-2 space-y-1">
              <label
                className="text-foreground text-xs font-medium"
                htmlFor="ols-notes"
              >
                Notes
              </label>
              <textarea
                id="ols-notes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={2}
                className="border-border bg-background text-foreground placeholder:text-muted-foreground focus-visible:ring-ring flex w-full rounded-md border px-3 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2"
              />
            </div>
            {errorMessage && (
              <div className="text-destructive col-span-2 text-xs">
                {errorMessage}
              </div>
            )}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {mutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Opening…
                </>
              ) : (
                "Open position"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="space-y-1">
      <label className="text-foreground text-xs font-medium" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
    </div>
  );
}

function SourcePill({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "h-8 flex-1 rounded-md border px-3 text-xs transition-colors",
        selected
          ? "border-primary/40 bg-primary/15 text-primary-foreground"
          : "border-border bg-background text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
