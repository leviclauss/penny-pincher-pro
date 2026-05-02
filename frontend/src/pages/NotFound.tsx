import { Link } from "react-router-dom";

export function NotFound(): JSX.Element {
  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center text-center">
      <p className="text-primary text-xs font-semibold uppercase tracking-widest">
        404
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">Page not found</h1>
      <p className="text-muted-foreground mt-2 max-w-sm text-sm">
        That route doesn't exist yet. The dashboard is the place to start.
      </p>
      <Link
        to="/"
        className="bg-primary text-primary-foreground hover:bg-primary/90 mt-6 inline-flex h-9 items-center rounded-md px-4 text-sm font-medium transition-colors"
      >
        Back to dashboard
      </Link>
    </div>
  );
}
