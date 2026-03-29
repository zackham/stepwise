import { Plus, Brain, Bot, UserCheck, Search } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface FlowTemplate {
  id: string;
  label: string;
  description: string;
  icon: LucideIcon;
  stepCount: number;
  yaml: string;
}

export const FLOW_TEMPLATES: FlowTemplate[] = [
  {
    id: "blank",
    label: "Blank",
    description: "Empty flow with a single hello step",
    icon: Plus,
    stepCount: 1,
    yaml: `name: my-flow
description: ""

steps:
  hello:
    run: 'echo "{\\"message\\": \\"hello\\"}"'
    outputs: [message]
`,
  },
  {
    id: "simple-llm",
    label: "Simple LLM",
    description: "Single LLM step with a prompt input",
    icon: Brain,
    stepCount: 1,
    yaml: `name: my-flow
description: Single LLM step

steps:
  generate:
    executor: llm
    config:
      prompt: "$prompt"
    inputs:
      prompt: $job.prompt
    outputs: [response]
`,
  },
  {
    id: "agent-task",
    label: "Agent Task",
    description: "Agent step with a validation loop",
    icon: Bot,
    stepCount: 2,
    yaml: `name: my-flow
description: Agent task with validation

steps:
  implement:
    executor: agent
    prompt: "Implement: $spec"
    inputs:
      spec: $job.spec
    outputs: [result]

  validate:
    run: |
      echo '{"status": "pass"}'
    inputs:
      result: implement.result
    outputs: [status]
    exits:
      - name: success
        when: "outputs.status == 'pass'"
        action: advance
      - name: retry
        when: "attempt < 3"
        action: loop
        target: implement
`,
  },
  {
    id: "external-approval",
    label: "Human Approval",
    description: "Agent draft with external approval loop",
    icon: UserCheck,
    stepCount: 2,
    yaml: `name: my-flow
description: Agent task with external approval loop

steps:
  draft:
    executor: agent
    prompt: "Draft: $request"
    inputs:
      request: $job.request
      feedback:
        from: approve.feedback
        optional: true
    outputs: [result]

  approve:
    executor: external
    prompt: "Review the draft and approve or request changes"
    inputs:
      result: draft.result
    outputs: [decision, feedback]
    exits:
      - name: approved
        when: "outputs.decision == 'approve'"
        action: advance
      - name: revise
        when: "attempt < 5"
        action: loop
        target: draft
`,
  },
  {
    id: "research-pipeline",
    label: "Research Pipeline",
    description: "Gather, analyze, and review with human gate",
    icon: Search,
    stepCount: 3,
    yaml: `name: my-flow
description: Multi-step research pipeline

steps:
  gather:
    executor: agent
    prompt: "Research the following topic and gather key findings: $topic"
    inputs:
      topic: $job.topic
    outputs: [findings]

  analyze:
    executor: llm
    config:
      prompt: "Analyze and synthesize these findings into a structured report:\\n$findings"
    inputs:
      findings: gather.findings
    outputs: [analysis]

  review:
    executor: external
    prompt: "Review the research analysis and approve or request deeper investigation"
    inputs:
      analysis: analyze.analysis
    outputs: [decision, notes]
    exits:
      - name: approved
        when: "outputs.decision == 'approve'"
        action: advance
      - name: dig-deeper
        when: "attempt < 3"
        action: loop
        target: gather
`,
  },
];
