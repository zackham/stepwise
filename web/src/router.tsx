import {
  createRouter,
  createRoute,
  createRootRoute,
  redirect,
} from "@tanstack/react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { JobDashboard } from "@/pages/JobDashboard";
import { JobDetailPage } from "@/pages/JobDetailPage";
import { JobEventsPage } from "@/pages/JobEventsPage";
import { JobTreePage } from "@/pages/JobTreePage";
import { JobTimelinePage } from "@/pages/JobTimelinePage";
import { FlowsPage } from "@/pages/FlowsPage";
import { EditorPage } from "@/pages/EditorPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { CanvasPage } from "@/pages/CanvasPage";
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

type JobsRouteSearch = {
  q?: string;
  status?: "running" | "awaiting_input" | "paused" | "completed" | "failed" | "pending" | "cancelled";
  range?: "today" | "7d" | "30d";
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
  component: JobDashboard,
  validateSearch: validateJobsSearch,
});

// Job detail
const jobDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId",
  component: JobDetailPage,
  validateSearch: validateJobsSearch,
});

// Job events
const jobEventsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId/events",
  component: JobEventsPage,
});

// Job tree
const jobTreeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId/tree",
  component: JobTreePage,
});

// Job timeline
const jobTimelineRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId/timeline",
  component: JobTimelinePage,
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

// Canvas (orchestrator overview)
const canvasRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/canvas",
  component: CanvasPage,
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
