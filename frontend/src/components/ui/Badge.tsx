import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium tracking-tight",
  {
    variants: {
      variant: {
        default: "border-border bg-muted text-muted-foreground",
        success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
        warning: "border-amber-500/30 bg-amber-500/10 text-amber-400",
        destructive: "border-red-500/30 bg-red-500/10 text-red-400",
        outline: "border-border text-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps): JSX.Element {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
