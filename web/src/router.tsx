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
import { BuilderPage } from "@/pages/BuilderPage";

// Root route
const rootRoute = createRootRoute({
  component: AppLayout,
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

// Builder
const builderRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/builder",
  component: BuilderPage,
});

// Builder with template
const builderTemplateRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/builder/$templateName",
  component: BuilderPage,
});

// Route tree
const routeTree = rootRoute.addChildren([
  indexRoute,
  jobsRoute,
  jobDetailRoute,
  jobEventsRoute,
  jobTreeRoute,
  builderRoute,
  builderTemplateRoute,
]);

export const router = createRouter({ routeTree });

// Register types
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
