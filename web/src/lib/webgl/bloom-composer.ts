import type { WebGLRenderer, Scene, Camera } from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { Vector2 } from "three";

export interface BloomComposer {
  composer: EffectComposer;
  resize(width: number, height: number): void;
  dispose(): void;
}

/**
 * Create EffectComposer with RenderPass + UnrealBloomPass.
 * Bloom parameters tuned for bright cyan energy on dark backgrounds.
 */
export function createBloomComposer(
  renderer: WebGLRenderer,
  scene: Scene,
  camera: Camera,
  width: number,
  height: number,
): BloomComposer {
  const composer = new EffectComposer(renderer);
  const renderPass = new RenderPass(scene, camera);
  composer.addPass(renderPass);

  const bloomPass = new UnrealBloomPass(
    new Vector2(width, height),
    1.5,  // strength — high for lots of glow
    0.8,  // radius — wide bloom spread
    0.2,  // threshold — low so most bright edges emit bloom
  );
  composer.addPass(bloomPass);

  return {
    composer,
    resize(w: number, h: number) {
      composer.setSize(w, h);
      bloomPass.resolution.set(w, h);
    },
    dispose() {
      composer.dispose();
    },
  };
}
