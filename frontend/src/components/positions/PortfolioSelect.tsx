import { useQuery } from "@tanstack/react-query";
import { fetchPortfolios } from "@/api/client";
import { cn } from "@/lib/utils";

interface Props {
  value: number | null;
  onChange: (id: number | null) => void;
  id?: string;
}

export function PortfolioSelect({ value, onChange, id }: Props): JSX.Element {
  const { data: portfolios } = useQuery({
    queryKey: ["portfolios"],
    queryFn: fetchPortfolios,
  });

  return (
    <select
      id={id}
      value={value ?? ""}
      onChange={(e) => {
        const v = e.target.value;
        onChange(v === "" ? null : Number(v));
      }}
      className={cn(
        "border-border bg-background text-foreground",
        "focus-visible:ring-ring flex h-9 w-full rounded-md border px-3 py-1 text-sm",
        "transition-colors focus-visible:outline-none focus-visible:ring-2",
        "disabled:cursor-not-allowed disabled:opacity-50",
      )}
    >
      <option value="">No portfolio</option>
      {portfolios?.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  );
}
