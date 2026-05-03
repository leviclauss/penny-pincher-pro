import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import {
  assignShortPut,
  calledAway,
  closeCoveredCall,
  closeShares,
  closeShortPut,
  expireCoveredCall,
  expireShortPut,
  openCoveredCall,
} from "@/api/client";
import type { PositionOut } from "@/api/types";
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

export type TransitionKind =
  | "close_put"
  | "expire_put"
  | "assign_put"
  | "open_call"
  | "close_call"
  | "expire_call"
  | "called_away"
  | "close_shares";

interface Props {
  position: PositionOut;
  kind: TransitionKind | null;
  onClose: () => void;
}

interface KindMeta {
  title: string;
  description: string;
  fields: Array<"debit" | "expDate" | "strike" | "contracts" | "credit" | "fees" | "salePrice">;
  dateLabel: string;
  submitLabel: string;
}

const META: Record<TransitionKind, KindMeta> = {
  close_put: {
    title: "Close short put",
    description:
      "Buy back the put for a debit. Realized P&L = (credit − debit) × contracts × 100 − fees.",
    fields: ["debit", "fees"],
    dateLabel: "Closed on",
    submitLabel: "Close put",
  },
  expire_put: {
    title: "Expire short put",
    description: "Mark the put expired worthless. Full premium is kept.",
    fields: [],
    dateLabel: "Expired on",
    submitLabel: "Expire put",
  },
  assign_put: {
    title: "Assign short put",
    description:
      "Put is assigned: keep the premium, take 100× shares per contract at strike. Position moves to Long shares.",
    fields: [],
    dateLabel: "Assigned on",
    submitLabel: "Assign",
  },
  open_call: {
    title: "Open covered call",
    description:
      "Sell a call against the held shares. Cannot exceed available share count.",
    fields: ["expDate", "strike", "contracts", "credit", "fees"],
    dateLabel: "Opened on",
    submitLabel: "Open call",
  },
  close_call: {
    title: "Close covered call",
    description:
      "Buy back the call. Realized P&L = (credit − debit) × contracts × 100 − fees. Returns to Long shares.",
    fields: ["debit", "fees"],
    dateLabel: "Closed on",
    submitLabel: "Close call",
  },
  expire_call: {
    title: "Expire covered call",
    description: "Mark the call expired worthless. Returns to Long shares with premium kept.",
    fields: [],
    dateLabel: "Expired on",
    submitLabel: "Expire call",
  },
  called_away: {
    title: "Called away",
    description: "Shares are called at strike, cycle ends. Realized P&L includes share gain/loss.",
    fields: [],
    dateLabel: "Called on",
    submitLabel: "Called away",
  },
  close_shares: {
    title: "Sell shares",
    description: "Manually sell the shares without an open covered call. Cycle ends.",
    fields: ["salePrice", "fees"],
    dateLabel: "Closed on",
    submitLabel: "Sell shares",
  },
};

export function TransitionDialog({ position, kind, onClose }: Props): JSX.Element {
  const qc = useQueryClient();
  const meta = kind ? META[kind] : null;

  const [date, setDate] = useState(todayIso());
  const [debit, setDebit] = useState("");
  const [salePrice, setSalePrice] = useState("");
  const [fees, setFees] = useState("0");
  // covered-call open
  const [expDate, setExpDate] = useState("");
  const [strike, setStrike] = useState("");
  const [contracts, setContracts] = useState("1");
  const [credit, setCredit] = useState("");

  const mutation = useMutation({
    mutationFn: async (): Promise<PositionOut> => {
      if (!kind) throw new Error("no transition selected");
      const id = position.id;
      switch (kind) {
        case "close_put":
          return closeShortPut(id, {
            debit: Number(debit),
            closed_on: date,
            fees: fees ? Number(fees) : 0,
          });
        case "expire_put":
          return expireShortPut(id, { expired_on: date });
        case "assign_put":
          return assignShortPut(id, { assigned_on: date });
        case "open_call":
          return openCoveredCall(id, {
            expiration: expDate,
            strike: Number(strike),
            contracts: Number(contracts),
            credit: Number(credit),
            opened_on: date,
            fees: fees ? Number(fees) : 0,
          });
        case "close_call":
          return closeCoveredCall(id, {
            debit: Number(debit),
            closed_on: date,
            fees: fees ? Number(fees) : 0,
          });
        case "expire_call":
          return expireCoveredCall(id, { expired_on: date });
        case "called_away":
          return calledAway(id, { called_on: date });
        case "close_shares":
          return closeShares(id, {
            sale_price: Number(salePrice),
            closed_on: date,
            fees: fees ? Number(fees) : 0,
          });
      }
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["positions"] });
      void qc.invalidateQueries({ queryKey: ["position", position.id] });
      onClose();
    },
  });

  useEffect(() => {
    if (kind === null) {
      setDate(todayIso());
      setDebit("");
      setSalePrice("");
      setFees("0");
      setExpDate("");
      setStrike("");
      setContracts("1");
      setCredit("");
      mutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);

  if (!meta || !kind) {
    return (
      <Dialog open={false} onOpenChange={() => onClose()}>
        <DialogContent>
          <></>
        </DialogContent>
      </Dialog>
    );
  }

  const submit = (e: React.FormEvent): void => {
    e.preventDefault();
    mutation.mutate();
  };

  const showField = (f: KindMeta["fields"][number]): boolean => meta.fields.includes(f);

  return (
    <Dialog open={kind !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>
              {meta.title} · {position.symbol}
            </DialogTitle>
            <DialogDescription>{meta.description}</DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-3">
            <Field label={`${meta.dateLabel} *`} htmlFor="td-date">
              <Input
                id="td-date"
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                required
              />
            </Field>
            {showField("debit") && (
              <Field label="Debit per contract *" htmlFor="td-debit">
                <Input
                  id="td-debit"
                  type="number"
                  step="0.01"
                  min="0"
                  value={debit}
                  onChange={(e) => setDebit(e.target.value)}
                  placeholder="0.30"
                  required
                />
              </Field>
            )}
            {showField("salePrice") && (
              <Field label="Sale price per share *" htmlFor="td-sale">
                <Input
                  id="td-sale"
                  type="number"
                  step="0.01"
                  min="0"
                  value={salePrice}
                  onChange={(e) => setSalePrice(e.target.value)}
                  required
                />
              </Field>
            )}
            {showField("expDate") && (
              <Field label="Expiration *" htmlFor="td-exp">
                <Input
                  id="td-exp"
                  type="date"
                  value={expDate}
                  onChange={(e) => setExpDate(e.target.value)}
                  required
                />
              </Field>
            )}
            {showField("strike") && (
              <Field label="Strike *" htmlFor="td-strike">
                <Input
                  id="td-strike"
                  type="number"
                  step="0.5"
                  min="0"
                  value={strike}
                  onChange={(e) => setStrike(e.target.value)}
                  required
                />
              </Field>
            )}
            {showField("contracts") && (
              <Field label="Contracts *" htmlFor="td-contracts">
                <Input
                  id="td-contracts"
                  type="number"
                  step="1"
                  min="1"
                  value={contracts}
                  onChange={(e) => setContracts(e.target.value)}
                  required
                />
              </Field>
            )}
            {showField("credit") && (
              <Field label="Credit per contract *" htmlFor="td-credit">
                <Input
                  id="td-credit"
                  type="number"
                  step="0.01"
                  min="0"
                  value={credit}
                  onChange={(e) => setCredit(e.target.value)}
                  required
                />
              </Field>
            )}
            {showField("fees") && (
              <Field label="Fees" htmlFor="td-fees">
                <Input
                  id="td-fees"
                  type="number"
                  step="0.01"
                  min="0"
                  value={fees}
                  onChange={(e) => setFees(e.target.value)}
                />
              </Field>
            )}
            {mutation.error && (
              <div className="text-destructive col-span-2 text-xs">
                {mutation.error.message}
              </div>
            )}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Submitting…
                </>
              ) : (
                meta.submitLabel
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
