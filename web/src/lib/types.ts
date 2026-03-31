// ── Enums ──────────────────────────────────────────────────────────────

export type JobStatus =
  | "staged"
  | "awaiting_approval"
  | "pending"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"
  | "archived";

export type StepRunStatus =
  | "running"
  | "suspended"
  | "delegated"
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped"
  | "throttled"
  | "waiting_reset";

export interface ThrottleInfo {
  executor_type: string;
  running: number;
  limit: number;
}

// ── Serializable References ────────────────────────────────────────────

export interface DecoratorRef {
  type: string; // "timeout", "retry", "notification", "fallback"
  config: Record<string, unknown>;
}

export interface ExecutorRef {
  type: string; // "script", "mock_llm", "external", etc.
  config: Record<string, unknown>;
  decorators: DecoratorRef[];
}

export interface ExitRule {
  name: string;
  type: string; // "field_match", "always"
  config: Record<string, unknown>;
  priority: number;
}

// ── Input Binding ──────────────────────────────────────────────────────

export interface InputBinding {
  local_name: string;
  source_step: string; // "$job" or step name; empty string for any_of
  source_field: string; // empty string for any_of
  any_of_sources?: { step: string; field: string }[];
}

// ── Step Limits ────────────────────────────────────────────────────────

export interface StepLimits {
  max_cost_usd: number | null;
  max_duration_minutes: number | null;
  max_iterations: number | null;
}

// ── For-Each Spec ─────────────────────────────────────────────────────

export interface ForEachSpec {
  source_step: string;
  source_field: string;
  item_var: string;
  on_error: string; // "fail_fast" | "continue"
}

// ── Step Definition ────────────────────────────────────────────────────

export interface StepDefinition {
  name: string;
  description: string;
  outputs: string[];
  executor: ExecutorRef;
  inputs: InputBinding[];
  after: string[];
  exit_rules: ExitRule[];
  idempotency: string;
  when?: string;
  limits: StepLimits | null;
  for_each?: ForEachSpec;
  sub_flow?: FlowDefinition;
  output_schema?: Record<string, OutputFieldSchema>;
  chain?: string;
  chain_label?: string;
}

// ── Flow Metadata ────────────────────────────────────────────────────

export interface FlowMetadata {
  name?: string;
  description?: string;
  author?: string;
  version?: string;
  tags?: string[];
  forked_from?: string;
  visibility?: "interactive" | "background" | "internal";
}

// ── Config Variable ──────────────────────────────────────────────────

export interface ConfigVar {
  name: string;
  description?: string;
  type?: "str" | "text" | "number" | "bool" | "choice";
  default?: unknown;
  required?: boolean;
  example?: string;
  options?: string[];
  sensitive?: boolean;
}

// ── Flow Definition ───────────────────────────────────────────────────

export interface FlowDefinition {
  steps: Record<string, StepDefinition>;
  metadata?: FlowMetadata;
  config_vars?: ConfigVar[];
}

// ── Sidecar ────────────────────────────────────────────────────────────

export interface Sidecar {
  decisions_made: string[];
  assumptions: string[];
  open_questions: string[];
  constraints_discovered: string[];
}

// ── HandoffEnvelope ────────────────────────────────────────────────────

export interface HandoffEnvelope {
  artifact: Record<string, unknown>;
  sidecar: Sidecar;
  executor_meta: Record<string, unknown>;
  workspace: string;
  timestamp: string;
}

// ── Output Schema ─────────────────────────────────────────────────────

export interface OutputFieldSchema {
  type: "str" | "text" | "number" | "bool" | "choice";
  required?: boolean;       // default true
  default?: unknown;
  description?: string;
  options?: string[];       // choice
  multiple?: boolean;       // choice multi-select
  min?: number;             // number
  max?: number;             // number
}

export type OutputSchema = Record<string, OutputFieldSchema>;

// ── WatchSpec ──────────────────────────────────────────────────────────

export interface WatchSpec {
  mode: string; // "poll", "external", "timeout"
  config: Record<string, unknown>;
  fulfillment_outputs: string[];
  output_schema?: OutputSchema;
}

// ── SubJobDefinition ───────────────────────────────────────────────────

export interface SubJobDefinition {
  objective: string;
  workflow: FlowDefinition;
  config: JobConfig | null;
}

// ── StepRun ────────────────────────────────────────────────────────────

export interface StepRun {
  id: string;
  job_id: string;
  step_name: string;
  attempt: number;
  status: StepRunStatus;
  inputs: Record<string, unknown> | null;
  dep_run_ids: Record<string, string> | null;
  result: HandoffEnvelope | null;
  error: string | null;
  error_category: string | null;
  traceback: string | null;
  executor_state: Record<string, unknown> | null;
  watch: WatchSpec | null;
  sub_job_id: string | null;
  started_at: string | null;
  completed_at: string | null;
}

// ── Step Event ────────────────────────────────────────────────────────

export interface StepEvent {
  id: number;
  run_id: string;
  timestamp: string;
  type: string;
  data: Record<string, unknown>;
}

// ── JobConfig ──────────────────────────────────────────────────────────

export interface JobConfig {
  max_sub_job_depth: number;
  timeout_minutes: number | null;
  metadata: Record<string, unknown>;
}

// ── Job ────────────────────────────────────────────────────────────────

export interface JobCurrentStep {
  name: string;
  status: string;
  started_at: string | null;
  completed_at?: string | null;
}

export interface Job {
  id: string;
  objective: string;
  name: string | null;
  workflow: FlowDefinition;
  status: JobStatus;
  inputs: Record<string, unknown>;
  parent_job_id: string | null;
  parent_step_run_id: string | null;
  workspace_path: string;
  config: JobConfig;
  created_at: string;
  updated_at: string;
  created_by: string;
  runner_pid: number | null;
  heartbeat_at: string | null;
  has_suspended_steps?: boolean;
  current_step?: JobCurrentStep | null;
  job_group: string | null;
  depends_on: string[];
  flow_source_path?: string | null;
}

// ── Event ──────────────────────────────────────────────────────────────

export interface StepwiseEvent {
  id: string;
  job_id: string;
  timestamp: string;
  type: string;
  data: Record<string, unknown>;
  is_effector: boolean;
}

// ── Group Settings ────────────────────────────────────────────────────

export interface GroupInfo {
  group: string;
  max_concurrent: number;
  active_count: number;
  pending_count: number;
  total_count: number;
}

// ── Job Tree ───────────────────────────────────────────────────────────

export interface JobTreeNode {
  job: Job;
  runs: StepRun[];
  sub_jobs: JobTreeNode[];
}

// ── Template ───────────────────────────────────────────────────────────

export interface FlowTemplate {
  name: string;
  description: string;
  workflow: FlowDefinition;
  created_at: string;
}

// ── Quick Launch ──────────────────────────────────────────────────────

export interface QuickLaunchItem {
  flow_name: string;
  flow_path: string | null;
  last_inputs: Record<string, unknown>;
  last_job_id: string;
  last_job_name: string | null;
  last_run_at: string;
  last_status: JobStatus;
  workflow: FlowDefinition;
}

// ── Engine Status ──────────────────────────────────────────────────────

export interface EngineStatus {
  active_jobs: number;
  total_jobs: number;
  registered_executors: string[];
  cwd?: string;
  version?: string;
}

// ── Agent Streaming ───────────────────────────────────────────────────

export type AgentStreamEvent =
  | { t: "text"; text: string }
  | { t: "tool_start"; id: string; title: string; kind: string }
  | { t: "tool_end"; id: string; output?: string; error?: boolean }
  | { t: "usage"; used: number; size: number };

export interface AgentOutputMessage {
  type: "agent_output";
  run_id: string;
  events: AgentStreamEvent[];
}

// ── Script Output Streaming ─────────────────────────────────────────

export interface ScriptOutputMessage {
  type: "script_output";
  run_id: string;
  stdout: string;
  stderr: string;
  stdout_offset: number;
  stderr_offset: number;
}

// ── WebSocket Message ──────────────────────────────────────────────────

export interface TickMessage {
  type: "tick";
  changed_jobs: string[];
  timestamp: string;
}

export interface FlowSourceChangedMessage {
  type: "flow_source_changed";
  job_ids: string[];
  timestamp: string;
}

export type WebSocketMessage = TickMessage | AgentOutputMessage | ScriptOutputMessage | FlowSourceChangedMessage;

// ── Editor / Local Flow ──────────────────────────────────────────────

export interface LocalFlow {
  path: string;
  name: string;
  description: string;
  steps_count: number;
  modified_at: string;
  is_directory: boolean;
  executor_types: string[];
  visibility: "interactive" | "background" | "internal";
  source?: "local" | "registry";
  registry_ref?: string;
}

export interface FlowGraphNode {
  id: string;
  label: string;
  executor_type: string;
  outputs: string[];
  details: Record<string, unknown>;
}

export interface FlowGraphEdge {
  source: string;
  target: string;
  label?: string;
  is_loop?: boolean;
}

export interface FlowGraph {
  nodes: FlowGraphNode[];
  edges: FlowGraphEdge[];
}

export interface LocalFlowDetail {
  path: string;
  name: string;
  raw_yaml: string;
  flow: FlowDefinition;
  graph: FlowGraph;
  is_directory: boolean;
  flow_dir: string;
}

export interface ParseResult {
  flow: FlowDefinition | null;
  graph: FlowGraph | null;
  errors: string[];
  raw_yaml?: string;
}

// ── Registry ──────────────────────────────────────────────────────────

export interface RegistryFlow {
  name: string;
  slug: string;
  author: string;
  version: number;
  description: string;
  tags: string[];
  yaml?: string;
  steps: number;
  loops: number;
  has_for_each: boolean;
  executor_types: string[];
  downloads: number;
  featured: boolean;
  graph?: FlowGraph;
  flow?: FlowDefinition | null;
}

export interface RegistrySearchResult {
  flows: RegistryFlow[];
  total: number;
}

// ── Event type constants ───────────────────────────────────────────────

export const EVENT_TYPES = {
  // Step lifecycle
  STEP_STARTED: "step.started",
  STEP_STARTED_ASYNC: "step.started_async",
  STEP_COMPLETED: "step.completed",
  STEP_FAILED: "step.failed",
  STEP_SUSPENDED: "step.suspended",
  STEP_DELEGATED: "step.delegated",
  STEP_CANCELLED: "step.cancelled",
  STEP_LIMIT_EXCEEDED: "step.limit_exceeded",
  // Job lifecycle
  JOB_STARTED: "job.started",
  JOB_COMPLETED: "job.completed",
  JOB_FAILED: "job.failed",
  JOB_PAUSED: "job.paused",
  JOB_RESUMED: "job.resumed",
  // Engine actions
  EXIT_RESOLVED: "exit.resolved",
  WATCH_FULFILLED: "watch.fulfilled",
  EXTERNAL_RERUN: "external.rerun",
  LOOP_ITERATION: "loop.iteration",
  LOOP_MAX_REACHED: "loop.max_reached",
  CONTEXT_INJECTED: "context.injected",
} as const;
