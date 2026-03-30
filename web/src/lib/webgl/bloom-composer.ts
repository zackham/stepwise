import type { WebGLRenderer, Scene, Camera } from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { Vector2 } from "three";

/**
 * Custom final pass: converts luminance to alpha.
 * After bloom, bright pixels become opaque, dark pixels become transparent.
 * This lets the WebGL canvas overlay transparently on any background.
 */
const LumaAlphaShader = {
  uniforms: {
    tDiffuse: { value: null },
  },
  vertexShader: `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    uniform sampler2D tDiffuse;
    varying vec2 vUv;
    void main() {
      vec4 texel = texture2D(tDiffuse, vUv);
      float luma = dot(texel.rgb, vec3(0.299, 0.587, 0.114));
      float alpha = smoothstep(0.01, 0.8, luma);
      gl_FragColor = vec4(texel.rgb, alpha);
    }
  `,
};

export interface BloomComposer {
  composer: EffectComposer;
  resize(width: number, height: number): void;
  dispose(): void;
}

/**
 * Create EffectComposer with RenderPass + UnrealBloomPass + LumaAlpha pass.
 * The luma-alpha pass converts brightness to transparency so the canvas
 * can overlay any background without a visible rectangle.
 */
export function createBloomComposer(
  renderer: WebGLRenderer,
  scene: Scene,
  camera: Camera,
  width: number,
  height: number,
): BloomComposer {
  const dpr = renderer.getPixelRatio();
  const composer = new EffectComposer(renderer);
  composer.setPixelRatio(dpr);
  const renderPass = new RenderPass(scene, camera);
  composer.addPass(renderPass);

  const bloomPass = new UnrealBloomPass(
    new Vector2(width * dpr, height * dpr),
    1.5,  // strength
    0.8,  // radius
    0.1,  // threshold
  );
  composer.addPass(bloomPass);

  // Convert luminance to alpha — dark = transparent, bright = opaque
  const alphaPass = new ShaderPass(LumaAlphaShader);
  alphaPass.needsSwap = true;
  composer.addPass(alphaPass);

  return {
    composer,
    resize(w: number, h: number) {
      composer.setSize(w, h);
      bloomPass.resolution.set(w * dpr, h * dpr);
    },
    dispose() {
      composer.dispose();
    },
  };
}
