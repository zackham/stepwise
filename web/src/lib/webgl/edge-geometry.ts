import {
  CatmullRomCurve3,
  Vector3,
  TubeGeometry,
} from "three";

/**
 * Convert dagre edge points to a smooth CatmullRom spline in the z=0 plane.
 * For 2-point edges, inserts a midpoint to give the spline enough control points.
 */
export function createEdgeCurve(
  points: Array<{ x: number; y: number }>,
): CatmullRomCurve3 {
  let pts = points.map((p) => new Vector3(p.x, p.y, 0));

  // CatmullRom needs at least 3 points for a smooth curve
  if (pts.length === 2) {
    const mid = new Vector3().lerpVectors(pts[0], pts[1], 0.5);
    pts = [pts[0], mid, pts[1]];
  }

  return new CatmullRomCurve3(pts, false, "centripetal", 0.5);
}

/**
 * Generate tube geometry from a curve.
 * Segment count is proportional to curve length for consistent detail.
 * Default radius: 1.5 (thin lines).
 */
export function createEdgeGeometry(
  curve: CatmullRomCurve3,
  radius: number = 1.5,
): TubeGeometry {
  const length = curve.getLength();
  const segments = Math.max(16, Math.min(128, Math.round(length / 3)));
  return new TubeGeometry(curve, segments, radius, 4, false);
}

/**
 * Convert loop edge geometry (SVG cubic Bezier) to a Three.js CatmullRom curve.
 * Parses the SVG path `M x y C cx1 cy1, cx2 cy2, ex ey` and samples it.
 * Returns null on malformed input — caller should skip the mesh and let SVG render the edge.
 */
export function createLoopEdgeCurve(
  svgPath: string,
): CatmullRomCurve3 | null {
  // Parse M x y C cx1 cy1, cx2 cy2, ex ey
  const nums = svgPath.match(/-?[\d.]+/g);
  if (!nums || nums.length < 8) {
    return null;
  }

  const [mx, my, cx1, cy1, cx2, cy2, ex, ey] = nums.map(Number);

  // Sample the cubic bezier at N points
  const sampleCount = 16;
  const pts: Vector3[] = [];
  for (let i = 0; i <= sampleCount; i++) {
    const t = i / sampleCount;
    const t2 = t * t;
    const t3 = t2 * t;
    const mt = 1 - t;
    const mt2 = mt * mt;
    const mt3 = mt2 * mt;

    const x = mt3 * mx + 3 * mt2 * t * cx1 + 3 * mt * t2 * cx2 + t3 * ex;
    const y = mt3 * my + 3 * mt2 * t * cy1 + 3 * mt * t2 * cy2 + t3 * ey;
    pts.push(new Vector3(x, y, 0));
  }

  return new CatmullRomCurve3(pts, false, "centripetal", 0.5);
}
