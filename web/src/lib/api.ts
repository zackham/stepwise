import type {
  Job,
  StepRun,
  StepEvent,
  StepwiseEvent,
  JobTreeNode,
  WorkflowTemplate,
  EngineStatus,
  WorkflowDefinition,
  JobConfig,
  AgentStreamEvent,
} from "./types";

const BASE_URL = "/api";

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ── Jobs ──────────────────────────────────────────────────────────────

export function fetchJobs(status?: string, topLevel?: boolean): Promise<Job[]> {
  const searchParams = new URLSearchParams();
  if (status) searchParams.set("status", status);
  if (topLevel) searchParams.set("top_level", "true");
  const qs = searchParams.toString();
  return request<Job[]>(`/jobs${qs ? `?${qs}` : ""}`);
}

export function fetchJob(jobId: string): Promise<Job> {
  return request<Job>(`/jobs/${jobId}`);
}

export function createJob(data: {
  objective: string;
  workflow: WorkflowDefinition;
  inputs?: Record<string, unknown>;
  config?: Partial<JobConfig>;
  workspace_path?: string;
}): Promise<Job> {
  return request<Job>("/jobs", {
    method: "POST",
    body: JSON.stringify({
      objective: data.objective,
      workflow: data.workflow,
      inputs: data.inputs ?? null,
      config: data.config ?? null,
      workspace_path: data.workspace_path ?? null,
    }),
  });
}

export function startJob(jobId: string): Promise<{ status: string }> {
  return request(`/jobs/${jobId}/start`, { method: "POST" });
}

export function pauseJob(jobId: string): Promise<{ status: string }> {
  return request(`/jobs/${jobId}/pause`, { method: "POST" });
}

export function resumeJob(jobId: string): Promise<{ status: string }> {
  return request(`/jobs/${jobId}/resume`, { method: "POST" });
}

export function cancelJob(jobId: string): Promise<{ status: string }> {
  return request(`/jobs/${jobId}/cancel`, { method: "POST" });
}

export function fetchJobTree(jobId: string): Promise<JobTreeNode> {
  return request<JobTreeNode>(`/jobs/${jobId}/tree`);
}

export function fetchRuns(
  jobId: string,
  stepName?: string
): Promise<StepRun[]> {
  const params = stepName ? `?step_name=${encodeURIComponent(stepName)}` : "";
  return request<StepRun[]>(`/jobs/${jobId}/runs${params}`);
}

export function rerunStep(
  jobId: string,
  stepName: string
): Promise<StepRun> {
  return request<StepRun>(
    `/jobs/${jobId}/steps/${encodeURIComponent(stepName)}/rerun`,
    { method: "POST" }
  );
}

export function fulfillWatch(
  runId: string,
  payload: Record<string, unknown>
): Promise<{ status: string }> {
  return request(`/runs/${runId}/fulfill`, {
    method: "POST",
    body: JSON.stringify({ payload }),
  });
}

export function fetchStepEvents(
  runId: string,
  limit?: number
): Promise<StepEvent[]> {
  const params = limit ? `?limit=${limit}` : "";
  return request<StepEvent[]>(`/runs/${runId}/step-events${params}`);
}

export function fetchRunCost(
  runId: string
): Promise<{ run_id: string; cost_usd: number }> {
  return request(`/runs/${runId}/cost`);
}

export function cancelRun(
  runId: string
): Promise<{ status: string; run_id: string }> {
  return request(`/runs/${runId}/cancel`, { method: "POST" });
}

export function deleteJob(jobId: string): Promise<{ status: string }> {
  return request(`/jobs/${jobId}`, { method: "DELETE" });
}

export function fetchAgentOutput(
  runId: string
): Promise<{ events: AgentStreamEvent[] }> {
  return request<{ events: AgentStreamEvent[] }>(`/runs/${runId}/agent-output`);
}

export function injectContext(
  jobId: string,
  context: string
): Promise<{ status: string }> {
  return request(`/jobs/${jobId}/context`, {
    method: "POST",
    body: JSON.stringify({ context }),
  });
}

export function fetchEvents(
  jobId: string,
  since?: string
): Promise<StepwiseEvent[]> {
  const params = since ? `?since=${encodeURIComponent(since)}` : "";
  return request<StepwiseEvent[]>(`/jobs/${jobId}/events${params}`);
}

// ── Engine ────────────────────────────────────────────────────────────

export function triggerTick(): Promise<{ status: string }> {
  return request("/tick", { method: "POST" });
}

export function fetchStatus(): Promise<EngineStatus> {
  return request<EngineStatus>("/status");
}

export function fetchExecutors(): Promise<{ executors: string[] }> {
  return request<{ executors: string[] }>("/executors");
}

// ── Templates ─────────────────────────────────────────────────────────

export function saveTemplate(data: {
  name: string;
  description?: string;
  workflow: WorkflowDefinition;
}): Promise<WorkflowTemplate> {
  return request<WorkflowTemplate>("/templates", {
    method: "POST",
    body: JSON.stringify({
      name: data.name,
      description: data.description ?? "",
      workflow: data.workflow,
    }),
  });
}

export function fetchTemplates(): Promise<WorkflowTemplate[]> {
  return request<WorkflowTemplate[]>("/templates");
}

export function fetchTemplate(name: string): Promise<WorkflowTemplate> {
  return request<WorkflowTemplate>(
    `/templates/${encodeURIComponent(name)}`
  );
}

export function deleteTemplate(name: string): Promise<{ status: string }> {
  return request(`/templates/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}
