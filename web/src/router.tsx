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
  validateSearch: (search: Record<string, unknown>): { q?: string; status?: string } => ({
    q: typeof search.q === "string" && search.q ? search.q : undefined,
    status: typeof search.status === "string" && search.status ? search.status : undefined,
  }),
});

// Job detail
const jobDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/jobs/$jobId",
  component: JobDetailPage,
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
