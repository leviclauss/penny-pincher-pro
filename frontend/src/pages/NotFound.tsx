import { Link } from "react-router-dom";

export function NotFound(): JSX.Element {
  return (
    <div className="text-center">
      <h1 className="mb-2 text-2xl font-semibold">Page not found</h1>
      <p className="text-muted-foreground mb-4 text-sm">
        That route doesn't exist yet.
      </p>
      <Link to="/" className="text-primary hover:underline">
        Back to dashboard
      </Link>
    </div>
  );
}
