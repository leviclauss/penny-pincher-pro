import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { openShortPut } from "@/api/client";
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
import { todayIso } from "@/lib/positions";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function OpenShortPutDialog({ open, onOpenChange }: Props): JSX.Element {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [symbol, setSymbol] = useState("");
  const [expiration, setExpiration] = useState("");
  const [strike, setStrike] = useState("");
  const [contracts, setContracts] = useState("1");
  const [credit, setCredit] = useState("");
  const [openedOn, setOpenedOn] = useState(todayIso());
  const [fees, setFees] = useState("0");
  const [notes, setNotes] = useState("");

  const mutation = useMutation({
    mutationFn: openShortPut,
    onSuccess: (position) => {
      void qc.invalidateQueries({ queryKey: ["positions"] });
      onOpenChange(false);
      navigate(`/positions/${position.id}`);
    },
  });

  useEffect(() => {
    if (!open) {
      setSymbol("");
      setExpiration("");
      setStrike("");
      setContracts("1");
      setCredit("");
      setOpenedOn(todayIso());
      setFees("0");
      setNotes("");
      mutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const submit = (e: React.FormEvent): void => {
    e.preventDefault();
    if (!symbol.trim() || !expiration || !strike || !credit || !openedOn) return;
    mutation.mutate({
      symbol: symbol.trim().toUpperCase(),
      expiration,
      strike: Number(strike),
      contracts: Number(contracts),
      credit: Number(credit),
      opened_on: openedOn,
      fees: fees ? Number(fees) : 0,
      notes: notes.trim() || null,
    });
  };

  const errorMessage = mutation.error?.message ?? null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Open short put</DialogTitle>
            <DialogDescription>
              Record a cash-secured put you've sold. The credit is the premium
              per contract (e.g. 1.25 = $125 per contract).
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Symbol *" htmlFor="osp-symbol">
              <Input
                id="osp-symbol"
                autoFocus
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="NVDA"
                maxLength={16}
                required
              />
            </Field>
            <Field label="Opened on *" htmlFor="osp-opened">
              <Input
                id="osp-opened"
                type="date"
                value={openedOn}
                onChange={(e) => setOpenedOn(e.target.value)}
                required
              />
            </Field>
            <Field label="Expiration *" htmlFor="osp-exp">
              <Input
                id="osp-exp"
                type="date"
                value={expiration}
                onChange={(e) => setExpiration(e.target.value)}
                required
              />
            </Field>
            <Field label="Strike *" htmlFor="osp-strike">
              <Input
                id="osp-strike"
                type="number"
                step="0.5"
                min="0"
                value={strike}
                onChange={(e) => setStrike(e.target.value)}
                placeholder="120"
                required
              />
            </Field>
            <Field label="Contracts *" htmlFor="osp-contracts">
              <Input
                id="osp-contracts"
                type="number"
                step="1"
                min="1"
                value={contracts}
                onChange={(e) => setContracts(e.target.value)}
                required
              />
            </Field>
            <Field label="Credit per contract *" htmlFor="osp-credit">
              <Input
                id="osp-credit"
                type="number"
                step="0.01"
                min="0"
                value={credit}
                onChange={(e) => setCredit(e.target.value)}
                placeholder="1.25"
                required
              />
            </Field>
            <Field label="Fees" htmlFor="osp-fees">
              <Input
                id="osp-fees"
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
                htmlFor="osp-notes"
              >
                Notes
              </label>
              <textarea
                id="osp-notes"
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
            <Button type="submit" disabled={mutation.isPending}>
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
