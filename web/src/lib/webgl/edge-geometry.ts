import {
  CurvePath,
  LineCurve3,
  Vector3,
  QuadraticBezierCurve3,
  CubicBezierCurve3,
  BufferGeometry,
  Float32BufferAttribute,
} from "three";
import type { Curve } from "three";

/**
 * Convert dagre edge points to a curve path matching SVG buildPath() exactly.
 */
export function createEdgeCurve(
  points: Array<{ x: number; y: number }>,
): CurvePath<Vector3> {
  const path = new CurvePath<Vector3>();
  if (points.length < 2) return path;

  if (points.length === 2) {
    path.add(new LineCurve3(
      new Vector3(points[0].x, points[0].y, 0),
      new Vector3(points[1].x, points[1].y, 0),
    ));
    return path;
  }

  if (points.length === 3) {
    path.add(new QuadraticBezierCurve3(
      new Vector3(points[0].x, points[0].y, 0),
      new Vector3(points[1].x, points[1].y, 0),
      new Vector3(points[2].x, points[2].y, 0),
    ));
    return path;
  }

  const start = points[0];
  const rest = points.slice(1);

  for (let i = 0; i < rest.length; i++) {
    const ctrl = i === 0 ? start : rest[i - 1];
    const p = rest[i];

    if (i === rest.length - 1) {
      if (i === 0) {
        path.add(new LineCurve3(
          new Vector3(start.x, start.y, 0),
          new Vector3(p.x, p.y, 0),
        ));
      } else {
        const prevCtrl = rest[i - 1];
        const segStart = new Vector3(
          (rest[Math.max(0, i - 2)].x + prevCtrl.x) / 2,
          (rest[Math.max(0, i - 2)].y + prevCtrl.y) / 2,
          0,
        );
        const mid = new Vector3((prevCtrl.x + p.x) / 2, (prevCtrl.y + p.y) / 2, 0);
        path.add(new QuadraticBezierCurve3(
          segStart,
          new Vector3(prevCtrl.x, prevCtrl.y, 0),
          mid,
        ));
        path.add(new LineCurve3(mid, new Vector3(p.x, p.y, 0)));
      }
    } else if (i === 0) {
      const mid = new Vector3((start.x + p.x) / 2, (start.y + p.y) / 2, 0);
      path.add(new QuadraticBezierCurve3(
        new Vector3(start.x, start.y, 0),
        new Vector3(start.x, start.y, 0),
        mid,
      ));
    } else {
      const prev = rest[i - 1];
      const mid = new Vector3((prev.x + p.x) / 2, (prev.y + p.y) / 2, 0);
      const prevPrev = i >= 2 ? rest[i - 2] : start;
      const segStart = new Vector3((prevPrev.x + prev.x) / 2, (prevPrev.y + prev.y) / 2, 0);
      path.add(new QuadraticBezierCurve3(
        segStart,
        new Vector3(prev.x, prev.y, 0),
        mid,
      ));
    }
  }

  return path;
}

/**
 * Generate a flat ribbon (2D strip) along a curve.
 * Unlike TubeGeometry (3D cylinder), this produces a perfectly smooth
 * flat surface with no polygon edge aliasing when viewed top-down.
 *
 * UV mapping: u = 0..1 along curve, v = 0..1 across width.
 */
export function createEdgeGeometry(
  curve: Curve<Vector3>,
  radius: number = 1.5,
): BufferGeometry {
  const length = curve.getLength();
  const segments = Math.max(64, Math.min(512, Math.round(length)));

  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];

  const halfWidth = radius;

  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const point = curve.getPointAt(t);
    // Get tangent to compute perpendicular
    const tangent = curve.getTangentAt(t);
    // Perpendicular in 2D: rotate tangent 90 degrees (swap x/y, negate one)
    const nx = -tangent.y;
    const ny = tangent.x;
    const len = Math.sqrt(nx * nx + ny * ny) || 1;

    // Two vertices per segment point: left and right of center
    positions.push(
      point.x + (nx / len) * halfWidth,
      point.y + (ny / len) * halfWidth,
      0,
    );
    positions.push(
      point.x - (nx / len) * halfWidth,
      point.y - (ny / len) * halfWidth,
      0,
    );

    uvs.push(t, 0);
    uvs.push(t, 1);
  }

  // Build triangle strip as indexed triangles
  for (let i = 0; i < segments; i++) {
    const a = i * 2;
    const b = i * 2 + 1;
    const c = (i + 1) * 2;
    const d = (i + 1) * 2 + 1;
    indices.push(a, b, c);
    indices.push(b, d, c);
  }

  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);

  return geometry;
}

/**
 * Convert loop edge SVG cubic Bezier to a Three.js CubicBezierCurve3.
 */
export function createLoopEdgeCurve(
  svgPath: string,
): CubicBezierCurve3 | null {
  const nums = svgPath.match(/-?[\d.]+/g);
  if (!nums || nums.length < 8) {
    return null;
  }

  const [mx, my, cx1, cy1, cx2, cy2, ex, ey] = nums.map(Number);

  return new CubicBezierCurve3(
    new Vector3(mx, my, 0),
    new Vector3(cx1, cy1, 0),
    new Vector3(cx2, cy2, 0),
    new Vector3(ex, ey, 0),
  );
}
