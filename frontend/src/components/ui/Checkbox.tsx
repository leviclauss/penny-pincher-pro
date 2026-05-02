import * as React from "react";
import { cn } from "@/lib/utils";

export interface CheckboxProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: React.ReactNode;
}

export const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, label, id, ...props }, ref) => {
    const generatedId = React.useId();
    const inputId = id ?? generatedId;
    const input = (
      <input
        ref={ref}
        id={inputId}
        type="checkbox"
        className={cn(
          "border-border bg-background text-primary focus-visible:ring-ring",
          "h-4 w-4 cursor-pointer rounded border accent-current",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
          "disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      />
    );
    if (!label) return input;
    return (
      <label
        htmlFor={inputId}
        className="text-foreground inline-flex cursor-pointer select-none items-center gap-2 text-sm"
      >
        {input}
        <span>{label}</span>
      </label>
    );
  },
);
Checkbox.displayName = "Checkbox";
