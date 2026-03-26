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
}

export interface GroupCluster {
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  completedCount: number;
  totalCount: number;
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
 * Jobs are connected by parent_job_id relationships.
 * Jobs in the same job_group are clustered together.
 */
export function computeCanvasLayout(jobs: Job[]): CanvasLayoutResult {
  if (jobs.length === 0) {
    return { cards: [], edges: [], groups: [], width: 0, height: 0 };
  }

  const { width: cardW, height: cardH } = cardSize(jobs.length);
  const jobIds = new Set(jobs.map((j) => j.id));

  // Build dependency map from parent_job_id
  const parentEdges: Array<{ from: string; to: string }> = [];
  for (const job of jobs) {
    if (job.parent_job_id && jobIds.has(job.parent_job_id)) {
      parentEdges.push({ from: job.parent_job_id, to: job.id });
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

  for (const edge of parentEdges) {
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
  for (const edge of parentEdges) {
    const fromCard = cardMap.get(edge.from);
    const toCard = cardMap.get(edge.to);
    if (fromCard && toCard) {
      edges.push({
        from: edge.from,
        to: edge.to,
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
      const pad = 16;
      groups.push({
        label,
        x: minX - pad,
        y: minY - pad - 24, // extra space for label
        width: maxX - minX + pad * 2,
        height: maxY - minY + pad * 2 + 24,
        completedCount: groupJobs.filter((j) => j.status === "completed").length,
        totalCount: groupJobs.length,
      });
    }
  }

  const graphMeta = g.graph();
  return {
    cards,
    edges,
    groups,
    width: (graphMeta?.width as number) || 800,
    height: (graphMeta?.height as number) || 600,
  };
}
