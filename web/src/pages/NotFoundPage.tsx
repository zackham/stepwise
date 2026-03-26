import { Link } from "@tanstack/react-router";

export function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-zinc-500">
      <span className="text-4xl font-bold text-zinc-600">404</span>
      <p className="text-sm">Page not found</p>
      <Link
        to="/jobs"
        className="text-sm text-blue-400 hover:text-blue-300 underline underline-offset-2"
      >
        Back to Jobs
      </Link>
    </div>
  );
}
