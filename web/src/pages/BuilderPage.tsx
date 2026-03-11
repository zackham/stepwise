import { useParams, useNavigate } from "@tanstack/react-router";
import { WorkflowBuilder } from "@/components/builder/WorkflowBuilder";
import { useTemplates } from "@/hooks/useStepwise";
import { useMemo } from "react";

export function BuilderPage() {
  const navigate = useNavigate();
  // Try to get templateName param if it exists
  let templateName: string | undefined;
  try {
    const params = useParams({ from: "/builder/$templateName" });
    templateName = params.templateName;
  } catch {
    // Not on template route
  }

  const { data: templates = [] } = useTemplates();
  const template = useMemo(
    () =>
      templateName
        ? templates.find((t) => t.name === templateName)
        : undefined,
    [templateName, templates]
  );

  return (
    <WorkflowBuilder
      initialWorkflow={template?.workflow}
      initialName={template?.name ?? ""}
      onJobCreated={(jobId) =>
        navigate({ to: "/jobs/$jobId", params: { jobId } })
      }
    />
  );
}
