import type {
  Job,
  StepRun,
  StepEvent,
  StepwiseEvent,
  JobTreeNode,
  FlowTemplate,
  EngineStatus,
  FlowDefinition,
  FlowMetadata,
  JobConfig,
  AgentStreamEvent,
  LocalFlow,
  LocalFlowDetail,
  ParseResult,
  RegistryFlow,
  RegistrySearchResult,
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
  workflow?: FlowDefinition | null;
  flow_path?: string;
  inputs?: Record<string, unknown>;
  config?: Partial<JobConfig>;
  workspace_path?: string;
}): Promise<Job> {
  return request<Job>("/jobs", {
    method: "POST",
    body: JSON.stringify({
      objective: data.objective,
      workflow: data.workflow ?? null,
      flow_path: data.flow_path ?? null,
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

export function adoptJob(jobId: string): Promise<{ status: string; job_id: string }> {
  return request(`/jobs/${jobId}/adopt`, { method: "POST" });
}

export function fetchStaleJobs(): Promise<Job[]> {
  return request<Job[]>("/jobs/stale");
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

export function deleteAllJobs(): Promise<{ status: string; count: number }> {
  return request("/jobs", { method: "DELETE" });
}

export function fetchJobCost(
  jobId: string
): Promise<{ job_id: string; cost_usd: number }> {
  return request(`/jobs/${jobId}/cost`);
}

export function fetchJobSuspended(
  jobId: string
): Promise<{ job_id: string; suspended_steps: Array<{ run_id: string; step: string; prompt: string; fields: string[] }> }> {
  return request(`/jobs/${jobId}/suspended`);
}

export function fetchJobOutput(
  jobId: string
): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/jobs/${jobId}/output`);
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
  workflow: FlowDefinition;
}): Promise<FlowTemplate> {
  return request<FlowTemplate>("/templates", {
    method: "POST",
    body: JSON.stringify({
      name: data.name,
      description: data.description ?? "",
      workflow: data.workflow,
    }),
  });
}

export function fetchTemplates(): Promise<FlowTemplate[]> {
  return request<FlowTemplate[]>("/templates");
}

export function fetchTemplate(name: string): Promise<FlowTemplate> {
  return request<FlowTemplate>(
    `/templates/${encodeURIComponent(name)}`
  );
}

export function deleteTemplate(name: string): Promise<{ status: string }> {
  return request(`/templates/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

// ── Editor / Local Flows ─────────────────────────────────────────────

export function fetchLocalFlows(): Promise<LocalFlow[]> {
  return request<LocalFlow[]>("/local-flows");
}

export function createLocalFlow(
  name: string
): Promise<{ path: string; name: string }> {
  return request<{ path: string; name: string }>("/local-flows", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function fetchLocalFlow(path: string): Promise<LocalFlowDetail> {
  return request<LocalFlowDetail>(`/flows/local/${path}`);
}

export function parseYaml(yaml: string): Promise<ParseResult> {
  return request<ParseResult>("/flows/parse", {
    method: "POST",
    body: JSON.stringify({ yaml }),
  });
}

export function deleteFlow(
  path: string
): Promise<{ status: string; path: string }> {
  return request(`/flows/local/${path}`, {
    method: "DELETE",
  });
}

export function saveFlow(
  path: string,
  yaml: string
): Promise<LocalFlowDetail> {
  return request<LocalFlowDetail>(`/flows/local/${path}`, {
    method: "PUT",
    body: JSON.stringify({ yaml }),
  });
}

export function patchStep(
  flowPath: string,
  stepName: string,
  changes: Record<string, unknown>
): Promise<ParseResult> {
  return request<ParseResult>("/flows/patch-step", {
    method: "POST",
    body: JSON.stringify({
      flow_path: flowPath,
      step_name: stepName,
      changes,
    }),
  });
}

export function addStep(
  flowPath: string,
  name: string,
  executor: string
): Promise<ParseResult> {
  return request<ParseResult>("/flows/add-step", {
    method: "POST",
    body: JSON.stringify({ flow_path: flowPath, name, executor }),
  });
}

export function deleteStep(
  flowPath: string,
  stepName: string
): Promise<ParseResult> {
  return request<ParseResult>("/flows/delete-step", {
    method: "POST",
    body: JSON.stringify({ flow_path: flowPath, step_name: stepName }),
  });
}

export function patchFlowMetadata(
  path: string,
  metadata: Partial<FlowMetadata>
): Promise<LocalFlowDetail> {
  return request<LocalFlowDetail>(`/flows/local/${path}`, {
    method: "PATCH",
    body: JSON.stringify(metadata),
  });
}

export function fetchFlowMtime(
  path: string
): Promise<{ mtime: number; modified_at: string }> {
  return request(`/flows/mtime?path=${encodeURIComponent(path)}`);
}

// ── Registry ──────────────────────────────────────────────────────────

export function searchRegistry(
  query: string = "",
  tag?: string,
  sort: string = "downloads",
  limit: number = 20
): Promise<RegistrySearchResult> {
  const params = new URLSearchParams({ sort, limit: String(limit) });
  if (query) params.set("q", query);
  if (tag) params.set("tag", tag);
  return request<RegistrySearchResult>(`/registry/search?${params}`);
}

export function fetchRegistryFlow(slug: string): Promise<RegistryFlow> {
  return request<RegistryFlow>(`/registry/flow/${encodeURIComponent(slug)}`);
}

export function installFlow(
  slug: string
): Promise<LocalFlowDetail & { errors: string[] }> {
  return request(`/registry/install`, {
    method: "POST",
    body: JSON.stringify({ slug }),
  });
}

// ── Flow Directory Files ─────────────────────────────────────────

export interface FlowFile {
  path: string;
  size: number;
  is_yaml: boolean;
}

export function fetchFlowFiles(
  flowPath: string
): Promise<{ flow_dir: string; files: FlowFile[] }> {
  return request(`/flows/local/${flowPath}/files`);
}

export function readFlowFile(
  flowPath: string,
  filePath: string
): Promise<{ path: string; content: string }> {
  return request(`/flows/local/${flowPath}/files/${filePath}`);
}

export function writeFlowFile(
  flowPath: string,
  filePath: string,
  content: string
): Promise<{ path: string; created: boolean; size: number }> {
  return request(`/flows/local/${flowPath}/files/${filePath}`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function deleteFlowFile(
  flowPath: string,
  filePath: string
): Promise<{ status: string; path: string }> {
  return request(`/flows/local/${flowPath}/files/${filePath}`, {
    method: "DELETE",
  });
}

// ── Config / Labels / Settings ───────────────────────────────────────

export interface LabelInfo {
  name: string;
  model: string;
  source: "default" | "user" | "project" | "local";
  is_default: boolean;
}

export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  context_length?: number;
  max_output_tokens?: number;
  prompt_cost?: number;       // USD per token (input)
  completion_cost?: number;   // USD per token (output)
}

export interface ConfigResponse {
  has_api_key: boolean;
  has_anthropic_key: boolean;
  api_key_source: string | null;
  model_registry: ModelInfo[];
  default_model: string;
  labels: LabelInfo[];
  billing_mode: string;
}

export function fetchConfig(): Promise<ConfigResponse> {
  return request<ConfigResponse>("/config");
}

export function fetchLabels(): Promise<{ labels: LabelInfo[]; default_model: string }> {
  return request("/config/labels");
}

export function createLabel(name: string, model: string): Promise<{ status: string }> {
  return request("/config/labels", {
    method: "POST",
    body: JSON.stringify({ name, model }),
  });
}

export function updateLabel(name: string, model: string): Promise<{ status: string }> {
  return request(`/config/labels/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({ model }),
  });
}

export function deleteLabel(name: string): Promise<{ status: string }> {
  return request(`/config/labels/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

export function addModel(model: Omit<ModelInfo, "id"> & { id: string }): Promise<{ status: string }> {
  return request("/config/models", {
    method: "POST",
    body: JSON.stringify(model),
  });
}

export function searchOpenRouterModels(q: string = "", limit: number = 30): Promise<{ models: ModelInfo[] }> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return request<{ models: ModelInfo[] }>(`/config/models/search?${params}`);
}

export function removeModel(modelId: string): Promise<{ status: string }> {
  return request(`/config/models/${encodeURIComponent(modelId)}`, {
    method: "DELETE",
  });
}

export function setApiKey(key: string, value: string, scope: string = "user"): Promise<{ status: string }> {
  return request("/config/api-key", {
    method: "PUT",
    body: JSON.stringify({ key, value, scope }),
  });
}

export function setDefaultModel(model: string): Promise<{ status: string }> {
  return request("/config/default-model", {
    method: "PUT",
    body: JSON.stringify({ model }),
  });
}

// ── Editor LLM Chat ──────────────────────────────────────────────────

export interface ChatChunk {
  type: "text" | "yaml" | "done" | "error" | "tool_use" | "tool_result" | "file_block" | "session" | "keepalive" | "files_changed";
  content?: string;
  apply_id?: string;
  model?: string;
  cost_usd?: number | null;
  // Agent tool use fields
  tool_name?: string;
  tool_input?: Record<string, string>;
  tool_use_id?: string;
  tool_output?: string;
  is_error?: boolean;
  input_tokens?: number;
  output_tokens?: number;
  // File block fields
  path?: string;
  paths?: string[];
  // Session persistence
  session_id?: string;
  // Tool metadata
  tool_kind?: string;
}

export async function* streamEditorChat(
  message: string,
  history: Array<{ role: string; content: string }>,
  currentYaml?: string,
  selectedStep?: string,
  agent?: string,
  sessionId?: string,
  flowPath?: string,
): AsyncGenerator<ChatChunk> {
  const res = await fetch(`${BASE_URL}/editor/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      history,
      current_yaml: currentYaml ?? null,
      selected_step: selectedStep ?? null,
      agent: agent ?? "claude",
      session_id: sessionId ?? null,
      flow_path: flowPath ?? null,
    }),
  });

  if (!res.ok) {
    yield { type: "error", content: `${res.status}: ${await res.text()}` };
    return;
  }

  const reader = res.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        yield JSON.parse(line) as ChatChunk;
      } catch {
        // skip malformed lines
      }
    }
  }

  // Process remaining buffer
  if (buffer.trim()) {
    try {
      yield JSON.parse(buffer) as ChatChunk;
    } catch {
      // skip
    }
  }
}
