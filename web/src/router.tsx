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
import { SchedulesPage } from "@/pages/SchedulesPage";
import { ScheduleDetailPage } from "@/pages/ScheduleDetailPage";
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
  status?: string; // comma-separated statuses e.g. "running,failed"
  range?: "today" | "7d" | "30d";
  view_mode?: "list" | "grid";
  hide_done?: "1";
};

function validateJobsSearch(search: Record<string, unknown>): JobsRouteSearch {
  // Validate comma-separated status values
  let status: string | undefined;
  if (typeof search.status === "string" && search.status.trim()) {
    const parts = search.status.split(",").filter((s) => JOB_ROUTE_STATUS_VALUES.has(s));
    status = parts.length > 0 ? parts.join(",") : undefined;
  }
  return {
    q: typeof search.q === "string" && search.q.trim() ? search.q : undefined,
    status,
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
    hide_done: search.hide_done === "1" ? "1" : undefined,
  };
}

const JOB_DETAIL_TAB_VALUES = new Set(["run", "step", "session"]);
const JOB_VIEW_VALUES = new Set(["dag", "events", "timeline", "tree"]);

export type JobDetailSearch = JobsRouteSearch & {
  step?: string;
  tab?: "run" | "step" | "session";
  panel?: "open";
  view?: "dag" | "events" | "timeline" | "tree";
  sidebar?: "0";
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
    sidebar: search.sidebar === "0" ? "0" : undefined,
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

// Flows list
const flowsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/flows",
  component: FlowsPage,
  validateSearch: (search: Record<string, unknown>): { selected?: string; kit?: string } => ({
    selected: typeof search.selected === "string" && search.selected ? search.selected : undefined,
    kit: typeof search.kit === "string" && search.kit ? search.kit : undefined,
  }),
});

// Flow editor
type EditorSearch = { step?: string };

function validateEditorSearch(search: Record<string, unknown>): EditorSearch {
  return {
    step: typeof search.step === "string" && search.step ? search.step : undefined,
  };
}

const flowEditorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/flows/$flowName",
  component: EditorPage,
  validateSearch: validateEditorSearch,
});

// Kit flow editor: /flows/kitName/flowName
const kitFlowEditorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/flows/$kitName/$flowName",
  component: EditorPage,
  validateSearch: validateEditorSearch,
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

// Schedules list
const schedulesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/schedules",
  component: SchedulesPage,
});

// Schedule detail
const scheduleDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/schedules/$scheduleId",
  component: ScheduleDetailPage,
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
  flowsRoute,
  kitFlowEditorRoute,
  flowEditorRoute,
  schedulesRoute,
  scheduleDetailRoute,
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
