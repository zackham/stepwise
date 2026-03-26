export interface ErrorGuidance {
  title: string;
  description: string;
  retryable: boolean;
  suggestions: string[];
}

export const ERROR_GUIDANCE: Record<string, ErrorGuidance> = {
  auth_error: {
    title: "Authentication Error",
    description: "The API key is invalid or lacks required permissions.",
    retryable: false,
    suggestions: [
      "Check API key configuration in Settings",
      "Verify the key has permissions for the requested model",
      "Ensure the key hasn't expired or been revoked",
    ],
  },
  quota_error: {
    title: "Quota / Billing Error",
    description: "API usage limit or billing threshold reached.",
    retryable: false,
    suggestions: [
      "Check billing status with your API provider",
      "Consider switching to a different model in the step config",
      "Wait for quota to reset if on a usage-based plan",
    ],
  },
  timeout: {
    title: "Timeout",
    description: "The operation timed out. This is usually transient.",
    retryable: true,
    suggestions: [
      "Retry — timeouts are often transient",
      "If persistent, increase the timeout via a timeout decorator",
      "Check if the upstream service is experiencing issues",
    ],
  },
  context_length: {
    title: "Context Length Exceeded",
    description: "Input exceeded the model's context window.",
    retryable: false,
    suggestions: [
      "Reduce the size of inputs being passed to this step",
      "Switch to a model with a larger context window",
      "Split the step into smaller sub-steps with less data each",
    ],
  },
  infra_failure: {
    title: "Infrastructure / Rate Limit",
    description: "Transient infrastructure issue (rate limit, server error).",
    retryable: true,
    suggestions: [
      "Retry — this is almost always transient",
      "If rate-limited, wait a moment before retrying",
      "Check provider status page if errors persist",
    ],
  },
  output_invalid: {
    title: "Invalid Output",
    description: "Step output doesn't match its declared outputs list.",
    retryable: false,
    suggestions: [
      "Check that the script/agent returns all declared output keys",
      "Verify JSON output format matches the step's outputs list",
      "Review the step's executor_meta for raw stdout/stderr",
    ],
  },
  executor_crash: {
    title: "Executor Crash",
    description: "The executor process crashed unexpectedly.",
    retryable: false,
    suggestions: [
      "Review stderr output in the run details for crash details",
      "Check the step's command or script for bugs",
      "Verify all required environment variables are set",
    ],
  },
  agent_failure: {
    title: "Agent Failure",
    description: "The agent step returned a failure status.",
    retryable: true,
    suggestions: [
      "Review the agent's output stream for error details",
      "Try injecting context with clarifications before retrying",
      "Check if the agent's working directory and tools are accessible",
    ],
  },
  user_cancelled: {
    title: "User Cancelled",
    description: "This step was cancelled by user action.",
    retryable: true,
    suggestions: [
      "Rerun the step if it was cancelled by mistake",
    ],
  },
  unknown: {
    title: "Unknown Error",
    description: "Unclassified error.",
    retryable: true,
    suggestions: [
      "Review the full error message for clues",
      "Check stderr/stdout in the run details",
      "If this is a recurring issue, consider adding exit rules for handling",
    ],
  },
};

export function getGuidance(category: string | null | undefined): ErrorGuidance {
  if (!category) {
    return {
      title: "Error",
      description: "No error category was assigned.",
      retryable: true,
      suggestions: ["Review the error message for details"],
    };
  }
  return ERROR_GUIDANCE[category] ?? ERROR_GUIDANCE["unknown"];
}
