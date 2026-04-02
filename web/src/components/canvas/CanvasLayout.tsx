import dagre from "dagre";
import type { Job } from "@/lib/types";

export interface CardPosition {
  jobId: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CardEdge {
  from: string;
  to: string;
  fromPos: { x: number; y: number };
  toPos: { x: number; y: number };
  satisfied: boolean;
}

export interface GroupCluster {
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  completedCount: number;
  totalCount: number;
  maxConcurrent: number;
  activeCount: number;
  pendingCount: number;
}

export interface CanvasLayoutResult {
  cards: CardPosition[];
  edges: CardEdge[];
  groups: GroupCluster[];
  width: number;
  height: number;
}

/** Compute card dimensions based on total job count. */
function cardSize(jobCount: number): { width: number; height: number } {
  if (jobCount <= 6) return { width: 300, height: 200 };
  if (jobCount <= 15) return { width: 280, height: 180 };
  return { width: 240, height: 160 };
}

/**
 * Compute spatial layout for all job cards using dagre.
 *
 * Jobs are connected by depends_on and parent_job_id relationships.
 * Jobs in the same job_group are clustered together.
 */
export function computeCanvasLayout(jobs: Job[], groupSettings?: Record<string, number>): CanvasLayoutResult {
  if (jobs.length === 0) {
    return { cards: [], edges: [], groups: [], width: 0, height: 0 };
  }

  const { width: cardW, height: cardH } = cardSize(jobs.length);
  const jobIds = new Set(jobs.map((j) => j.id));

  // Build status lookup for satisfied/blocking classification
  const statusMap = new Map<string, string>();
  for (const job of jobs) {
    statusMap.set(job.id, job.status);
  }

  // Build dependency edges from depends_on + parent_job_id
  const edgeSet = new Set<string>();
  const depEdges: Array<{ from: string; to: string; satisfied: boolean }> = [];
  for (const job of jobs) {
    // depends_on edges
    for (const depId of job.depends_on ?? []) {
      if (jobIds.has(depId)) {
        const key = `${depId}->${job.id}`;
        if (!edgeSet.has(key)) {
          edgeSet.add(key);
          const depStatus = statusMap.get(depId) ?? "pending";
          depEdges.push({ from: depId, to: job.id, satisfied: depStatus === "completed" });
        }
      }
    }
    // parent_job_id as fallback edge
    if (job.parent_job_id && jobIds.has(job.parent_job_id)) {
      const key = `${job.parent_job_id}->${job.id}`;
      if (!edgeSet.has(key)) {
        edgeSet.add(key);
        const depStatus = statusMap.get(job.parent_job_id) ?? "pending";
        depEdges.push({ from: job.parent_job_id, to: job.id, satisfied: depStatus === "completed" });
      }
    }
  }

  // Collect job groups
  const groupMap = new Map<string, Job[]>();
  for (const job of jobs) {
    const group = job.job_group ?? null;
    if (group) {
      if (!groupMap.has(group)) groupMap.set(group, []);
      groupMap.get(group)!.push(job);
    }
  }

  // Layout with dagre
  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "LR",
    nodesep: 40,
    ranksep: 80,
    marginx: 60,
    marginy: 60,
  });
  g.setDefaultEdgeLabel(() => ({}));

  for (const job of jobs) {
    g.setNode(job.id, { width: cardW, height: cardH });
  }

  for (const edge of depEdges) {
    g.setEdge(edge.from, edge.to);
  }

  dagre.layout(g);

  const cards: CardPosition[] = [];
  const cardMap = new Map<string, CardPosition>();

  for (const job of jobs) {
    const node = g.node(job.id);
    if (!node) continue;
    const pos: CardPosition = {
      jobId: job.id,
      x: node.x - cardW / 2,
      y: node.y - cardH / 2,
      width: cardW,
      height: cardH,
    };
    cards.push(pos);
    cardMap.set(job.id, pos);
  }

  // Build edges with positions
  const edges: CardEdge[] = [];
  for (const edge of depEdges) {
    const fromCard = cardMap.get(edge.from);
    const toCard = cardMap.get(edge.to);
    if (fromCard && toCard) {
      edges.push({
        from: edge.from,
        to: edge.to,
        satisfied: edge.satisfied,
        fromPos: {
          x: fromCard.x + fromCard.width,
          y: fromCard.y + fromCard.height / 2,
        },
        toPos: {
          x: toCard.x,
          y: toCard.y + toCard.height / 2,
        },
      });
    }
  }

  // Build group clusters
  const groups: GroupCluster[] = [];
  for (const [label, groupJobs] of groupMap) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const job of groupJobs) {
      const card = cardMap.get(job.id);
      if (!card) continue;
      minX = Math.min(minX, card.x);
      minY = Math.min(minY, card.y);
      maxX = Math.max(maxX, card.x + card.width);
      maxY = Math.max(maxY, card.y + card.height);
    }
    if (minX < Infinity) {
      const pad = 12;
      const labelH = 24;
      groups.push({
        label,
        x: minX - pad,
        y: minY - pad - labelH,
        width: maxX - minX + pad * 2,
        height: maxY - minY + pad * 2 + labelH,
        completedCount: groupJobs.filter((j) => j.status === "completed").length,
        totalCount: groupJobs.length,
        maxConcurrent: groupSettings?.[label] ?? 0,
        activeCount: groupJobs.filter((j) => j.status === "running" && !j.parent_job_id).length,
        pendingCount: groupJobs.filter((j) => j.status === "pending").length,
      });
    }
  }

  // Post-layout: push overlapping groups apart vertically
  const gap = 24;
  groups.sort((a, b) => a.y - b.y);
  for (let i = 1; i < groups.length; i++) {
    const prev = groups[i - 1];
    const prevBottom = prev.y + prev.height + gap;
    if (groups[i].y < prevBottom) {
      const shift = prevBottom - groups[i].y;
      groups[i].y += shift;
      // Shift all cards belonging to this group
      const groupJobIds = new Set(
        groupMap.get(groups[i].label)?.map((j) => j.id) ?? []
      );
      for (const card of cards) {
        if (groupJobIds.has(card.jobId)) {
          card.y += shift;
        }
      }
      // Update edges for shifted cards
      for (const edge of edges) {
        const fromCard = cardMap.get(edge.from);
        const toCard = cardMap.get(edge.to);
        if (fromCard) {
          edge.fromPos = { x: fromCard.x + fromCard.width, y: fromCard.y + fromCard.height / 2 };
        }
        if (toCard) {
          edge.toPos = { x: toCard.x, y: toCard.y + toCard.height / 2 };
        }
      }
    }
  }

  // Recompute total dimensions after shifts
  let totalW = 0, totalH = 0;
  for (const card of cards) {
    totalW = Math.max(totalW, card.x + card.width);
    totalH = Math.max(totalH, card.y + card.height);
  }
  for (const group of groups) {
    totalW = Math.max(totalW, group.x + group.width);
    totalH = Math.max(totalH, group.y + group.height);
  }

  const graphMeta = g.graph();
  return {
    cards,
    edges,
    groups,
    width: Math.max(totalW + 60, (graphMeta?.width as number) || 800),
    height: Math.max(totalH + 60, (graphMeta?.height as number) || 600),
  };
}
