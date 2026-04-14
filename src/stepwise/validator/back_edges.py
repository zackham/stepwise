"""Topological order + cycle / back-edge detection.

Uses Kahn's algorithm. Edges considered:
  - producer → consumer for each `after` entry
  - producer → consumer for each `after_any_of` group member
  - producer → consumer for each input binding (regular + any_of)

Step 7 (§11): ``compute_back_edges`` reads ``InputBinding.is_back_edge``
directly from the parsed flow. The yaml_loader pre-pass marks back-edge
bindings during parse, and the validator simply harvests the marking
into the (consumer, producer) tuple set used by ``mhb`` and the
forward-DAG residual cycle check in ``validate.validate``.
"""

from __future__ import annotations

from collections import defaultdict, deque

from stepwise.models import WorkflowDefinition


def _build_edges(
    flow: WorkflowDefinition,
    exclude_back_edges: bool = False,
) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Build the producer→consumer edge map and consumer in-degree counts.

    If ``exclude_back_edges`` is True:
      - bindings marked ``InputBinding.is_back_edge=True`` are skipped, AND
      - per-source back-edges within mixed-scope any_of bindings are
        excluded individually (computed via ``models.collect_loop_back_edges``)
        — necessary for the §11.4 mixed-scope case where the binding-as-a-
        whole is NOT marked but individual sources are loop-back.
    """
    from stepwise.models import collect_loop_back_edges

    per_source_back_edges: set[tuple[str, str]] = set()
    if exclude_back_edges:
        per_source_back_edges = collect_loop_back_edges(flow.steps)

    in_degree: dict[str, int] = {name: 0 for name in flow.steps}
    edges: dict[str, set[str]] = defaultdict(set)

    for name, step in flow.steps.items():
        # after dependencies
        for dep_name in step.after:
            if dep_name in flow.steps and name not in edges[dep_name]:
                edges[dep_name].add(name)
                in_degree[name] += 1
        # after_resolved dependencies
        for dep_name in step.after_resolved:
            if dep_name in flow.steps and name not in edges[dep_name]:
                edges[dep_name].add(name)
                in_degree[name] += 1
        # after_any_of (added in step 2)
        for group in step.after_any_of:
            for dep_name in group:
                if dep_name in flow.steps and name not in edges[dep_name]:
                    edges[dep_name].add(name)
                    in_degree[name] += 1
        # input bindings
        for binding in step.inputs:
            if exclude_back_edges and binding.is_back_edge:
                continue
            if binding.any_of_sources:
                for src_step, _ in binding.any_of_sources:
                    if (
                        exclude_back_edges
                        and (name, src_step) in per_source_back_edges
                    ):
                        continue
                    if src_step in flow.steps and name not in edges[src_step]:
                        edges[src_step].add(name)
                        in_degree[name] += 1
            elif (
                binding.source_step
                and binding.source_step != "$job"
                and binding.source_step in flow.steps
                and name not in edges[binding.source_step]
            ):
                if (
                    exclude_back_edges
                    and (name, binding.source_step) in per_source_back_edges
                ):
                    continue
                edges[binding.source_step].add(name)
                in_degree[name] += 1

    return edges, in_degree


def compute_topological_order(flow: WorkflowDefinition) -> list[str]:
    """Kahn's algorithm. Raises RuntimeError if a cycle is detected.

    Edges considered: producer → consumer for after, after_any_of, and
    every input binding (regular + any_of).
    """
    edges, in_degree = _build_edges(flow)
    queue = deque(name for name, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while queue:
        cur = queue.popleft()
        order.append(cur)
        for nxt in sorted(edges[cur]):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(flow.steps):
        unprocessed = set(flow.steps) - set(order)
        raise RuntimeError(f"cycle detected involving steps: {sorted(unprocessed)}")
    return order


def find_cycle_nodes(flow: WorkflowDefinition) -> set[str]:
    """Return the set of step names that participate in a cycle.

    Runs Kahn's algorithm. The leftover nodes (those not processable
    because their in-degree never reaches zero) are exactly the SCC
    members. Returns an empty set if the graph is acyclic.
    """
    edges, in_degree = _build_edges(flow)
    queue = deque(name for name, deg in in_degree.items() if deg == 0)
    processed: set[str] = set()
    while queue:
        cur = queue.popleft()
        processed.add(cur)
        for nxt in edges[cur]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    return set(flow.steps) - processed


def find_cycle_nodes_excluding_back_edges(flow: WorkflowDefinition) -> set[str]:
    """Like find_cycle_nodes() but ignores InputBinding.is_back_edge=True.

    Step 7 (§11): used by validator/validate.py to find RESIDUAL cycles
    in the forward DAG. A flow with well-formed loop-back bindings has
    its back-edges marked at parse time; the forward DAG (with those
    edges removed) must be acyclic. Any leftover cycle nodes here are
    genuine errors and get rejected with `cyclic_dependency`.
    """
    edges, in_degree = _build_edges(flow, exclude_back_edges=True)
    queue = deque(name for name, deg in in_degree.items() if deg == 0)
    processed: set[str] = set()
    while queue:
        cur = queue.popleft()
        processed.add(cur)
        for nxt in edges[cur]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    return set(flow.steps) - processed


def compute_back_edges(flow: WorkflowDefinition) -> set[tuple[str, str]]:
    """Return the set of (consumer_step, producer_step) back-edge pairs.

    Step 7 (§11): combines two sources of back-edge truth:
      1. Bindings marked ``InputBinding.is_back_edge=True`` by the
         yaml_loader pre-pass (whole-binding back-edges, including
         §11.4 same-loop-frame any_of cases).
      2. Per-source back-edges within mixed-scope any_of bindings —
         computed structurally via ``models.collect_loop_back_edges``
         so the residual cycle check and mhb computation see them too.
    """
    from stepwise.models import collect_loop_back_edges

    back_edges: set[tuple[str, str]] = set()
    for step_name, step in flow.steps.items():
        for binding in step.inputs:
            if not binding.is_back_edge:
                continue
            if binding.any_of_sources:
                for src_step, _ in binding.any_of_sources:
                    if src_step and src_step != "$job":
                        back_edges.add((step_name, src_step))
            elif binding.source_step and binding.source_step != "$job":
                back_edges.add((step_name, binding.source_step))

    # Per-source back-edges in mixed-scope any_of bindings (§11.4):
    # the binding-as-a-whole isn't marked, but individual sources are.
    back_edges.update(collect_loop_back_edges(flow.steps))
    return back_edges
