import type * as THREE from "three";

let _canUseWebGL: boolean | null = null;

/** Check if WebGL 2 is available (required by Three.js ≥r163). Result is cached after first call. */
export function canUseWebGL(): boolean {
  if (_canUseWebGL !== null) return _canUseWebGL;
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2");
    _canUseWebGL = gl !== null;
    if (gl) {
      const ext = gl.getExtension("WEBGL_lose_context");
      ext?.loseContext(); // release probe context immediately
    }
  } catch {
    _canUseWebGL = false;
  }
  return _canUseWebGL;
}

/** Recursively dispose all geometries and materials in a scene. */
export function disposeScene(scene: THREE.Scene): void {
  scene.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.geometry) {
      mesh.geometry.dispose();
    }
    if (mesh.material) {
      if (Array.isArray(mesh.material)) {
        for (const mat of mesh.material) mat.dispose();
      } else {
        mesh.material.dispose();
      }
    }
  });
}
