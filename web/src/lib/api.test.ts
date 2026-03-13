import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fetchJobs,
  fetchJob,
  createJob,
  startJob,
  fetchRuns,
  fulfillWatch,
  fetchEvents,
  fetchStatus,
  fetchTemplates,
  saveTemplate,
  deleteTemplate,
} from "./api";

// Mock global fetch
const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function jsonResponse(data: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  });
}

function errorResponse(status: number, detail: string) {
  return Promise.resolve({
    ok: false,
    status,
    json: () => Promise.resolve({ detail }),
    text: () => Promise.resolve(detail),
  });
}

describe("API client", () => {
  describe("fetchJobs", () => {
    it("fetches all jobs without filter", async () => {
      const jobs = [{ id: "j1", objective: "test" }];
      mockFetch.mockReturnValueOnce(jsonResponse(jobs));

      const result = await fetchJobs();

      expect(mockFetch).toHaveBeenCalledWith("/api/jobs", expect.objectContaining({
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
      }));
      expect(result).toEqual(jobs);
    });

    it("appends status filter as query param", async () => {
      mockFetch.mockReturnValueOnce(jsonResponse([]));

      await fetchJobs("running");

      expect(mockFetch).toHaveBeenCalledWith(
        "/api/jobs?status=running",
        expect.any(Object)
      );
    });
  });

  describe("fetchJob", () => {
    it("fetches a single job by ID", async () => {
      const job = { id: "j1", objective: "test" };
      mockFetch.mockReturnValueOnce(jsonResponse(job));

      const result = await fetchJob("j1");

      expect(mockFetch).toHaveBeenCalledWith("/api/jobs/j1", expect.any(Object));
      expect(result).toEqual(job);
    });
  });

  describe("createJob", () => {
    it("sends POST with correct body", async () => {
      const workflow = { steps: {} };
      const created = { id: "j2", objective: "new" };
      mockFetch.mockReturnValueOnce(jsonResponse(created));

      const result = await createJob({
        objective: "new",
        workflow,
        inputs: { key: "value" },
      });

      expect(mockFetch).toHaveBeenCalledWith("/api/jobs", expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          objective: "new",
          workflow,
          inputs: { key: "value" },
          config: null,
          workspace_path: null,
        }),
      }));
      expect(result).toEqual(created);
    });

    it("defaults inputs and config to null", async () => {
      const workflow = { steps: {} };
      mockFetch.mockReturnValueOnce(jsonResponse({ id: "j3" }));

      await createJob({ objective: "minimal", workflow });

      const lastCall = mockFetch.mock.lastCall!;
      const body = JSON.parse(lastCall[1].body);
      expect(body.inputs).toBeNull();
      expect(body.config).toBeNull();
      expect(body.workspace_path).toBeNull();
    });
  });

  describe("startJob", () => {
    it("sends POST to start endpoint", async () => {
      mockFetch.mockReturnValueOnce(jsonResponse({ status: "ok" }));

      await startJob("j1");

      expect(mockFetch).toHaveBeenCalledWith(
        "/api/jobs/j1/start",
        expect.objectContaining({ method: "POST" })
      );
    });
  });

  describe("fetchRuns", () => {
    it("fetches runs without step filter", async () => {
      mockFetch.mockReturnValueOnce(jsonResponse([]));

      await fetchRuns("j1");

      expect(mockFetch).toHaveBeenCalledWith("/api/jobs/j1/runs", expect.any(Object));
    });

    it("encodes step name in query param", async () => {
      mockFetch.mockReturnValueOnce(jsonResponse([]));

      await fetchRuns("j1", "step with spaces");

      expect(mockFetch).toHaveBeenCalledWith(
        "/api/jobs/j1/runs?step_name=step%20with%20spaces",
        expect.any(Object)
      );
    });
  });

  describe("fulfillWatch", () => {
    it("sends payload in correct format", async () => {
      mockFetch.mockReturnValueOnce(jsonResponse({ status: "ok" }));

      await fulfillWatch("r1", { answer: 42 });

      expect(mockFetch).toHaveBeenCalledWith(
        "/api/runs/r1/fulfill",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ payload: { answer: 42 } }),
        })
      );
    });
  });

  describe("fetchEvents", () => {
    it("fetches events without since param", async () => {
      mockFetch.mockReturnValueOnce(jsonResponse([]));

      await fetchEvents("j1");

      expect(mockFetch).toHaveBeenCalledWith("/api/jobs/j1/events", expect.any(Object));
    });

    it("includes since param when provided", async () => {
      mockFetch.mockReturnValueOnce(jsonResponse([]));

      await fetchEvents("j1", "2024-01-01T00:00:00Z");

      expect(mockFetch).toHaveBeenCalledWith(
        "/api/jobs/j1/events?since=2024-01-01T00%3A00%3A00Z",
        expect.any(Object)
      );
    });
  });

  describe("engine endpoints", () => {
    it("fetchStatus hits /status", async () => {
      const status = { active_jobs: 2, total_jobs: 10, registered_executors: [] };
      mockFetch.mockReturnValueOnce(jsonResponse(status));

      const result = await fetchStatus();

      expect(result).toEqual(status);
    });
  });

  describe("templates", () => {
    it("saveTemplate sends POST with body", async () => {
      const tmpl = { name: "test", description: "desc", workflow: { steps: {} } };
      mockFetch.mockReturnValueOnce(jsonResponse(tmpl));

      await saveTemplate({ name: "test", description: "desc", workflow: { steps: {} } });

      expect(mockFetch).toHaveBeenCalledWith(
        "/api/templates",
        expect.objectContaining({ method: "POST" })
      );
    });

    it("fetchTemplates hits /templates", async () => {
      mockFetch.mockReturnValueOnce(jsonResponse([]));

      await fetchTemplates();

      expect(mockFetch).toHaveBeenCalledWith("/api/templates", expect.any(Object));
    });

    it("deleteTemplate sends DELETE with encoded name", async () => {
      mockFetch.mockReturnValueOnce(jsonResponse({ status: "ok" }));

      await deleteTemplate("my template");

      expect(mockFetch).toHaveBeenCalledWith(
        "/api/templates/my%20template",
        expect.objectContaining({ method: "DELETE" })
      );
    });
  });

  describe("error handling", () => {
    it("throws on non-ok response with status and detail", async () => {
      mockFetch.mockReturnValueOnce(errorResponse(404, "Not found"));

      await expect(fetchJob("nonexistent")).rejects.toThrow("404: Not found");
    });

    it("throws on server error", async () => {
      mockFetch.mockReturnValueOnce(errorResponse(500, "Internal server error"));

      await expect(fetchJobs()).rejects.toThrow("500: Internal server error");
    });
  });
});
