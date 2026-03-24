"""Ephemeral flow composition — compile a linear chain of flows into a single workflow."""

from __future__ import annotations

import logging
from pathlib import Path

from stepwise.models import ConfigVar, WorkflowDefinition

logger = logging.getLogger(__name__)

# Priority order for mapping upstream `result` to a downstream config var name.
RESULT_BINDING_PRIORITY = ["spec", "topic", "prompt", "question"]


def compile_chain(flow_paths: list[Path], var_names: list[str]) -> str:
    """Compile a linear sequence of flows into an ephemeral chain workflow YAML.

    Args:
        flow_paths: Resolved absolute paths to each flow file (2+ required).
        var_names: Names of job-level variables (from --var flags) to pass through.

    Returns:
        YAML string for the ephemeral chain workflow.
    """
    from stepwise.yaml_loader import load_workflow_yaml
    from stepwise.flow_resolution import flow_display_name

    if len(flow_paths) < 2:
        raise ValueError("chain requires at least 2 flows")

    # Load each flow to inspect config vars and terminal outputs
    stages: list[_StageInfo] = []
    for i, fpath in enumerate(flow_paths):
        wf = load_workflow_yaml(str(fpath))
        display_name = flow_display_name(fpath)
        terminals = wf.terminal_steps()
        if not terminals:
            raise ValueError(
                f"Flow '{display_name}' has no terminal steps — "
                f"cannot determine outputs for chaining"
            )

        # Collect outputs from terminal steps
        terminal_outputs = _get_terminal_outputs(wf, terminals, display_name)

        # Scan $job.* refs from step input bindings
        job_refs = _scan_job_refs(wf)

        stages.append(_StageInfo(
            index=i + 1,
            flow_path=fpath.resolve(),
            display_name=display_name,
            config_vars=wf.config_vars,
            job_refs=job_refs,
            terminal_outputs=terminal_outputs,
        ))

    # Build the chain name
    name_parts = [s.display_name for s in stages]
    chain_name = "chain-" + "-".join(name_parts)
    if len(chain_name) > 80:
        chain_name = chain_name[:80]

    # Build description
    description = "Chain: " + " → ".join(name_parts)

    # Build YAML
    import yaml

    steps: dict[str, dict] = {}
    for i, stage in enumerate(stages):
        step_name = f"stage-{stage.index}"
        step: dict = {
            "description": stage.display_name,
            "flow": str(stage.flow_path),
            "outputs": list(stage.terminal_outputs),
        }

        # Build inputs
        inputs: dict[str, str] = {}

        # Wire result from previous stage
        if i > 0:
            prev_step = f"stage-{stages[i - 1].index}"
            # Determine which output from the previous stage to wire
            prev_outputs = stages[i - 1].terminal_outputs
            source_field = "result" if "result" in prev_outputs else list(prev_outputs)[0]
            if source_field != "result" and "result" not in prev_outputs:
                logger.warning(
                    "Stage %d (%s) has no 'result' output — using '%s' instead",
                    i, stages[i - 1].display_name, source_field,
                )

            # Determine which config var to bind the result to
            binding_name = _determine_result_binding(stage.config_vars, stage.job_refs)
            inputs[binding_name] = f"{prev_step}.{source_field}"

        # Passthrough job-level vars that match this flow's expected inputs
        config_var_names = {v.name for v in stage.config_vars}
        expected_inputs = config_var_names | stage.job_refs
        for var_name in var_names:
            if var_name in expected_inputs:
                # Don't override the result binding
                if var_name not in inputs:
                    inputs[var_name] = f"$job.{var_name}"
            elif not expected_inputs:
                # Flow has no config vars or job refs — pass all vars through
                if var_name not in inputs:
                    inputs[var_name] = f"$job.{var_name}"

        if inputs:
            step["inputs"] = inputs

        # Add after for ordering (inputs handle data deps, but if stage N
        # has no inputs from stage N-1 we still need ordering)
        if i > 0:
            prev_step = f"stage-{stages[i - 1].index}"
            # after is implicit via input binding, skip explicit

        steps[step_name] = step

    workflow = {
        "name": chain_name,
        "description": description,
        "steps": steps,
    }

    return yaml.dump(workflow, default_flow_style=False, sort_keys=False)


def _scan_job_refs(wf: WorkflowDefinition) -> set[str]:
    """Scan a workflow's step input bindings for $job.* references.

    Returns the set of field names that the flow expects from $job.
    Same pattern as models.py validate_warnings job_fields extraction.
    """
    return {
        b.source_field
        for step in wf.steps.values()
        for b in step.inputs
        if b.source_step == "$job"
    }


def _determine_result_binding(
    config_vars: list[ConfigVar], job_refs: set[str] | None = None,
) -> str:
    """Determine which config var name to wire upstream `result` into.

    Priority order:
    1. Check config_vars names against priority list (spec, topic, prompt, question)
    2. Check job_refs against same priority list
    3. First required config var (by declaration order)
    4. First job ref from priority list
    5. Fallback: 'result'
    """
    if job_refs is None:
        job_refs = set()

    cv_names = {v.name for v in config_vars}

    # Check config vars against priority list first
    for name in RESULT_BINDING_PRIORITY:
        if name in cv_names:
            return name

    # Check job refs against priority list
    for name in RESULT_BINDING_PRIORITY:
        if name in job_refs:
            return name

    # First required config var
    for v in config_vars:
        if v.required:
            return v.name

    # First config var (even if optional)
    if config_vars:
        return config_vars[0].name

    return "result"


def _get_terminal_outputs(
    wf: WorkflowDefinition, terminals: list[str], display_name: str,
) -> list[str]:
    """Collect output fields from terminal steps.

    If multiple terminals exist, use the union of all their outputs.
    Ensures 'result' is present (or warns if not).
    """
    all_outputs: list[str] = []
    seen: set[str] = set()
    for term_name in terminals:
        for out in wf.steps[term_name].outputs:
            if out not in seen:
                seen.add(out)
                all_outputs.append(out)

    if not all_outputs:
        raise ValueError(
            f"Flow '{display_name}' terminal steps have no declared outputs"
        )

    if "result" not in seen:
        logger.warning(
            "Flow '%s' terminal steps have no 'result' output — "
            "using '%s' as the chain output field",
            display_name, all_outputs[0],
        )

    return all_outputs


class _StageInfo:
    """Internal: metadata about one stage in the chain."""

    __slots__ = ("index", "flow_path", "display_name", "config_vars", "job_refs", "terminal_outputs")

    def __init__(
        self,
        index: int,
        flow_path: Path,
        display_name: str,
        config_vars: list[ConfigVar],
        job_refs: set[str],
        terminal_outputs: list[str],
    ):
        self.index = index
        self.flow_path = flow_path
        self.display_name = display_name
        self.config_vars = config_vars
        self.job_refs = job_refs
        self.terminal_outputs = terminal_outputs
