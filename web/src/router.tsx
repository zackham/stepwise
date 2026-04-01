import {
  createRouter,
  createRoute,
  createRootRoute,
  redirect,
} from "@tanstack/react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { JobsPage } from "@/pages/JobsPage";
import { JobDetailPage } from "@/pages/JobDetailPage";
import { FlowsPage } from "@/pages/FlowsPage";
import { EditorPage } from "@/pages/EditorPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

const JOB_ROUTE_STATUS_VALUES = new Set([
  "running",
  "awaiting_input",
  "paused",
  "completed",
  "failed",
  "pending",
  "cancelled",
]);

const JOB_ROUTE_RANGE_VALUES = new Set(["today", "7d", "30d", "all"]);
const JOB_VIEW_MODE_VALUES = new Set(["list", "grid"]);

type JobsRouteSearch = {
  q?: string;
  status?: "running" | "awaiting_input" | "paused" | "completed" | "failed" | "pending" | "cancelled";
  range?: "today" | "7d" | "30d";
  view_mode?: "list" | "grid";
};

function validateJobsSearch(search: Record<string, unknown>): JobsRouteSearch {
  return {
    q: typeof search.q === "string" && search.q.trim() ? search.q : undefined,
    status:
      typeof search.status === "string" && JOB_ROUTE_STATUS_VALUES.has(search.status)
        ? search.status as JobsRouteSearch["status"]
        : undefined,
    range:
      typeof search.range === "string"
      && JOB_ROUTE_RANGE_VALUES.has(search.range)
      && search.range !== "all"
        ? search.range as JobsRouteSearch["range"]
        : undefined,
    view_mode:
      typeof search.view_mode === "string" && JOB_VIEW_MODE_VALUES.has(search.view_mode)
        ? (search.view_mode as "list" | "grid")
        : undefined,
  };
}

const JOB_DETAIL_TAB_VALUES = new Set(["run", "step", "session"]);
const JOB_VIEW_VALUES = new Set(["dag", "events", "timeline", "tree"]);

export type JobDetailSearch = JobsRouteSearch & {
  step?: string;
  tab?: "run" | "step" | "session";
  panel?: "open";
  view?: "dag" | "events" | "timeline" | "tree";
};

function validateJobDetailSearch(search: Record<string, unknown>): JobDetailSearch {
  const base = validateJobsSearch(search);
  return {
    ...base,
    step: typeof search.step === "string" && search.step ? search.step : undefined,
    tab: typeof search.tab === "string" && JOB_DETAIL_TAB_VALUES.has(search.tab)
      ? search.tab as JobDetailSearch["tab"]
      : undefined,
    panel: search.panel === "open" ? "open" : undefined,
    view: typeof search.view === "string" && JOB_VIEW_VALUES.has(search.view)
      ? search.view as JobDetailSearch["view"]
      : undefined,
  };
}

// Root route
const rootRoute = createRootRoute({
  component: AppLayout,
  notFoundComponent: NotFoundPage,
});

// Index route (redirect to /jobs)
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/jobs" });
  },
});

// Jobs list
const jobsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs",
  component: JobsPage,
  validateSearch: validateJobsSearch,
});

// Job detail
const jobDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId",
  component: JobDetailPage,
  validateSearch: validateJobDetailSearch,
});

// Legacy route redirects: events/tree/timeline → job detail with view param
const jobEventsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId/events",
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/jobs/$jobId", params: { jobId: params.jobId }, search: { view: "events" } });
  },
});

const jobTreeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId/tree",
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/jobs/$jobId", params: { jobId: params.jobId }, search: { view: "tree" } });
  },
});

const jobTimelineRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId/timeline",
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/jobs/$jobId", params: { jobId: params.jobId }, search: { view: "timeline" } });
  },
});

// Flows list
const flowsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/flows",
  component: FlowsPage,
  validateSearch: (search: Record<string, unknown>): { selected?: string } => ({
    selected: typeof search.selected === "string" && search.selected ? search.selected : undefined,
  }),
});

// Flow editor
const flowEditorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/flows/$flowName",
  component: EditorPage,
});

// Legacy editor redirects
const editorRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/editor",
  beforeLoad: () => {
    throw redirect({ to: "/flows" });
  },
});

const editorFlowRedirectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/editor/$flowName",
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/flows/$flowName", params: { flowName: params.flowName } });
  },
});

// Canvas → redirect to Jobs grid view
const canvasRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/canvas",
  beforeLoad: () => {
    throw redirect({ to: "/jobs", search: { view_mode: "grid" as const } });
  },
});

// Settings
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: SettingsPage,
});

// Route tree
const routeTree = rootRoute.addChildren([
  indexRoute,
  jobsRoute,
  jobDetailRoute,
  jobEventsRoute,
  jobTreeRoute,
  jobTimelineRoute,
  flowsRoute,
  flowEditorRoute,
  editorRedirectRoute,
  editorFlowRedirectRoute,
  canvasRoute,
  settingsRoute,
]);

export const router = createRouter({ routeTree });

// Register types
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
