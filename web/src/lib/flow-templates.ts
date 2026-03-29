import { Plus, Brain, Bot, UserCheck, Search } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface FlowTemplate {
  id: string;
  label: string;
  description: string;
  icon: LucideIcon;
  stepCount: number;
}

export const FLOW_TEMPLATES: FlowTemplate[] = [
  {
    id: "blank",
    label: "Blank",
    description: "Empty flow with a single hello step",
    icon: Plus,
    stepCount: 1,
  },
  {
    id: "simple-llm",
    label: "Simple LLM",
    description: "Single LLM step with a prompt input",
    icon: Brain,
    stepCount: 1,
  },
  {
    id: "agent-task",
    label: "Agent Task",
    description: "Agent step with a validation loop",
    icon: Bot,
    stepCount: 2,
  },
  {
    id: "external-approval",
    label: "Human Approval",
    description: "Agent draft with external approval loop",
    icon: UserCheck,
    stepCount: 2,
  },
  {
    id: "research-pipeline",
    label: "Research Pipeline",
    description: "Gather, analyze, and review with human gate",
    icon: Search,
    stepCount: 3,
  },
];
