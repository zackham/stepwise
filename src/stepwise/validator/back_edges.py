"""Topological order + cycle / back-edge detection.

Uses Kahn's algorithm. Edges considered:
  - producer → consumer for each `after` entry
  - producer → consumer for each `after_any_of` group member
  - producer → consumer for each input binding (regular + any_of)

Step 3 always returns an empty back_edges set from compute_back_edges()
because loop-back binding semantics ship in step 7. Cycles are still
detected by find_cycle_nodes() and rejected by the validator with a
deferred message.
"""

from __future__ import annotations

from collections import defaultdict, deque

from stepwise.models import WorkflowDefinition


def _build_edges(flow: WorkflowDefinition) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Build the producer→consumer edge map and consumer in-degree counts."""
    in_degree: dict[str, int] = {name: 0 for name in flow.steps}
    edges: dict[str, set[str]] = defaultdict(set)

    for name, step in flow.steps.items():
        # after dependencies
        for dep_name in step.after:
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
            if binding.any_of_sources:
                for src_step, _ in binding.any_of_sources:
                    if src_step in flow.steps and name not in edges[src_step]:
                        edges[src_step].add(name)
                        in_degree[name] += 1
            elif (
                binding.source_step
                and binding.source_step != "$job"
                and binding.source_step in flow.steps
                and name not in edges[binding.source_step]
            ):
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


def compute_back_edges(flow: WorkflowDefinition) -> set[tuple[str, str]]:
    """For step 3: always returns empty set (loop-back binding runtime is step 7).

    Step 7 will replace this with structural detection: producer-after-
    consumer in topological order, closed by an enclosing loop exit
    rule.

    The validator's top-level validate(flow) separately calls
    find_cycle_nodes(flow) and rejects flows with any cycles in step 3.
    """
    return set()
