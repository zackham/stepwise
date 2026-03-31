import {
  Terminal,
  Globe,
  Brain,
  Bot,
  Cog,
  Layers,
  Repeat,
  Eye,
} from "lucide-react";

/**
 * Returns the appropriate icon for an executor type.
 * Pass `className` to control size (default: "w-4 h-4").
 */
export function executorIcon(type: string, className = "w-4 h-4") {
  switch (type) {
    case "script":
      return <Terminal className={className} />;
    case "external":
      return <Globe className={className} />;
    case "mock_llm":
    case "llm":
      return <Brain className={className} />;
    case "agent":
      return <Bot className={className} />;
    case "sub_flow":
      return <Layers className={className} />;
    case "for_each":
      return <Repeat className={className} />;
    case "poll":
      return <Eye className={className} />;
    default:
      return <Cog className={className} />;
  }
}

/** Human-readable label for an executor type. */
export function executorLabel(type: string): string {
  switch (type) {
    case "script":
      return "Script";
    case "external":
      return "External";
    case "mock_llm":
      return "Mock LLM";
    case "llm":
      return "LLM";
    case "agent":
      return "Agent";
    case "sub_flow":
      return "Sub-flow";
    case "for_each":
      return "For-each";
    case "poll":
      return "Poll";
    default:
      return type;
  }
}
