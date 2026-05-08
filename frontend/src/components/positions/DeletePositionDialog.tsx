import { useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { deletePosition } from "@/api/client";
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

interface Props {
  position: PositionOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function DeletePositionDialog({
  position,
  open,
  onOpenChange,
}: Props): JSX.Element {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const mutation = useMutation({
    mutationFn: () => deletePosition(position.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["positions"] });
      void qc.invalidateQueries({ queryKey: ["portfolios"] });
      onOpenChange(false);
      navigate("/positions");
    },
  });

  useEffect(() => {
    if (!open) mutation.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={(o) => !mutation.isPending && onOpenChange(o)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Delete closed position?</DialogTitle>
          <DialogDescription>
            This permanently removes <strong>{position.symbol}</strong> (cycle #
            {position.cycle_id ?? position.id}) along with its legs and
            snapshots. This cannot be undone.
          </DialogDescription>
        </DialogHeader>

        {mutation.error && (
          <div className="text-destructive text-xs">{mutation.error.message}</div>
        )}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? (
              <>
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                Deleting…
              </>
            ) : (
              "Delete"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
