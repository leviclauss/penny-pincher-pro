import { useParams } from "react-router-dom";

export function TickerDetail(): JSX.Element {
  const { symbol } = useParams<{ symbol: string }>();
  return (
    <div>
      <h1 className="mb-1 text-2xl font-semibold">{symbol}</h1>
      <p className="text-muted-foreground text-sm">Coming up next.</p>
    </div>
  );
}
