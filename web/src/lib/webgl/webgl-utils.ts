import type * as THREE from "three";

let _canUseWebGL: boolean | null = null;

/** Check if WebGL is available. Result is cached after first call. */
export function canUseWebGL(): boolean {
  if (_canUseWebGL !== null) return _canUseWebGL;
  try {
    const canvas = document.createElement("canvas");
    const gl =
      canvas.getContext("webgl2") || canvas.getContext("webgl");
    _canUseWebGL = gl !== null;
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
