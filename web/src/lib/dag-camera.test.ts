import { describe, it, expect } from "vitest";
import { DagCamera, DEAD_ZONE_FRACTION, TARGET_BLEND_MS } from "./dag-camera";
import type { Rect } from "./dag-camera";

const VIEWPORT = { width: 1000, height: 800 };

function makeRect(x: number, y: number, w = 240, h = 88): Rect {
  return { x, y, width: w, height: h };
}

/** Run camera for N seconds at 60fps, return final transform */
function simulate(
  camera: DagCamera,
  seconds: number,
): { x: number; y: number; scale: number; settled: boolean } {
  const frames = Math.round(seconds * 60);
  const dt = 1 / 60;
  let result = { x: 0, y: 0, scale: 1, settled: true };
  for (let i = 0; i < frames; i++) {
    result = camera.tick(dt);
  }
  return result;
}

describe("DagCamera", () => {
  it("spring converges to target without overshoot", () => {
    const camera = new DagCamera();
    camera.syncFromManualInput(0, 0, 1);

    // Place active node far to the right (outside dead zone)
    const rect = makeRect(800, 300);
    camera.setActiveNodes([rect], VIEWPORT);

    // Record positions over time — should converge monotonically
    const positions: number[] = [];
    for (let i = 0; i < 120; i++) {
      const { x } = camera.tick(1 / 60);
      positions.push(x);
    }

    // Should be settled by end (2 seconds is plenty)
    const last = positions[positions.length - 1];
    const secondToLast = positions[positions.length - 2];
    expect(Math.abs(last - secondToLast)).toBeLessThan(1);

    // Check no overshoot: positions should be monotonic (allow 1px spring tolerance)
    const direction = positions[0] < 0 ? -1 : 1;
    for (let i = 1; i < positions.length - 1; i++) {
      if (direction < 0) {
        expect(positions[i]).toBeLessThanOrEqual(positions[i - 1] + 1);
      } else {
        expect(positions[i]).toBeGreaterThanOrEqual(positions[i - 1] - 1);
      }
    }
  });

  it("dead zone: no movement when nodes are inside comfort zone", () => {
    const camera = new DagCamera();
    // Position camera so nodes are comfortably in the center
    camera.syncFromManualInput(200, 200, 1);

    // Node at canvas (300, 200) → screen (500, 400), well within dead zone
    const rect = makeRect(300, 200);
    camera.setActiveNodes([rect], VIEWPORT);

    const result = simulate(camera, 1);
    expect(result.x).toBeCloseTo(200, 0);
    expect(result.y).toBeCloseTo(200, 0);
    expect(result.settled).toBe(true);
  });

  it("dead zone exit: camera moves when nodes drift outside", () => {
    const camera = new DagCamera();
    camera.syncFromManualInput(0, 0, 1);

    // Node at x=50 → screenX=50, dead zone left=150 → outside by 100px
    const rect = makeRect(50, 300);
    camera.setActiveNodes([rect], VIEWPORT);

    const result = simulate(camera, 2);
    // Camera should have moved right (positive x) to bring node inside
    expect(result.x).toBeGreaterThan(50);
  });

  it("target blending: smooth transition when active set changes", () => {
    let now = 1000;
    const camera = new DagCamera(() => now);
    camera.syncFromManualInput(0, 0, 1);

    // First active set — inside dead zone so camera stays put
    const rect1 = makeRect(300, 250);
    camera.setActiveNodes([rect1], VIEWPORT);
    simulate(camera, 2);

    // Change to a different active set that requires movement
    now = 6000;
    const rect2 = makeRect(50, 50);
    camera.setActiveNodes([rect2], VIEWPORT);

    // Advance time to blend midpoint and tick
    now = 6000 + TARGET_BLEND_MS / 2;
    const mid = camera.tick(1 / 60);

    // Camera should not be settled — it's actively blending + springing
    expect(mid.settled).toBe(false);
  });

  it("zoom stability: no zoom change when nodes fit comfortably", () => {
    const camera = new DagCamera();
    camera.syncFromManualInput(200, 200, 0.8);

    const rects = [
      makeRect(100, 100),
      makeRect(100, 300),
      makeRect(100, 500),
    ];
    camera.setActiveNodes(rects, VIEWPORT);

    const result = simulate(camera, 2);
    // Zoom should stay close to initial 0.8 (nodes fit fine)
    expect(result.scale).toBeCloseTo(0.8, 1);
  });

  it("DT cap: no wild jumps after simulated tab switch", () => {
    const camera = new DagCamera();
    camera.syncFromManualInput(0, 0, 1);

    // Place node outside dead zone to create a target
    const rect = makeRect(50, 50);
    camera.setActiveNodes([rect], VIEWPORT);

    // Simulate a few normal frames
    for (let i = 0; i < 5; i++) camera.tick(1 / 60);
    const beforeJump = camera.getTransform();

    // Simulate a huge dt (like returning from a background tab after 5 seconds)
    const afterJump = camera.tick(5.0);

    // The position change should be bounded (DT_CAP = 0.05s worth of movement)
    const jumpDist = Math.sqrt(
      (afterJump.x - beforeJump.x) ** 2 + (afterJump.y - beforeJump.y) ** 2,
    );
    expect(jumpDist).toBeLessThan(50);
  });

  it("syncFromManualInput snaps immediately", () => {
    const camera = new DagCamera();
    camera.syncFromManualInput(100, 200, 0.75);
    const t = camera.getTransform();
    expect(t.x).toBe(100);
    expect(t.y).toBe(200);
    expect(t.scale).toBe(0.75);
    expect(camera.isSettled()).toBe(true);
  });

  it("zooms out when nodes overflow viewport", () => {
    const camera = new DagCamera();
    camera.syncFromManualInput(0, 0, 1);

    // Nodes spanning wider than viewport
    const rects = [
      makeRect(0, 300),
      makeRect(1200, 300), // 1200 + 240 = 1440px span, viewport is 1000
    ];
    camera.setActiveNodes(rects, VIEWPORT);

    const result = simulate(camera, 3);
    expect(result.scale).toBeLessThan(1);
    expect(result.scale).toBeGreaterThanOrEqual(0.3);
  });

  it("pan stays coherent during zoom animation (no jank)", () => {
    const camera = new DagCamera();
    // Start zoomed in with nodes that will force a zoom-out
    camera.syncFromManualInput(0, 0, 1);

    // Wide node spread forces zoom out from 1.0
    const rects = [
      makeRect(0, 200),
      makeRect(1100, 200),
    ];
    camera.setActiveNodes(rects, VIEWPORT);

    // Track screen-space position of node center each frame
    // If pan+zoom are coherent, screen position should move smoothly
    const screenPositions: number[] = [];
    const cx = (0 + 1100 + 240) / 2; // canvas center X of nodes
    for (let i = 0; i < 120; i++) {
      const t = camera.tick(1 / 60);
      const screenCx = cx * t.scale + t.x;
      screenPositions.push(screenCx);
    }

    // Check smoothness: no frame-to-frame jump > 15px in screen space
    for (let i = 1; i < screenPositions.length; i++) {
      const jump = Math.abs(screenPositions[i] - screenPositions[i - 1]);
      expect(jump).toBeLessThan(15);
    }
  });
});
