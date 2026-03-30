/**
 * GLSL shaders for energy pulse edge visualization.
 *
 * States:
 *   0 = Idle     — faint dim wire
 *   1 = Surge    — bright bolt traveling source→target
 *   2 = Flow     — continuous repeating energy pulses
 *   3 = Completed — brief flash then dim cyan glow
 *   4 = Failed   — red pulse then dim red
 *
 * Uniforms:
 *   u_hue  — 0.0 = cyan (data edges), 1.0 = orange (loop edges)
 *   u_dim  — 1.0 = normal, 0.5 = sequencing-only (dimmer)
 */

export const VERTEX_SHADER = /* glsl */ `
varying vec2 vUv;

void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

export const FRAGMENT_SHADER = /* glsl */ `
uniform float u_time;
uniform int u_state;
uniform float u_surge_progress;
uniform float u_curve_length;
uniform float u_flash;
uniform float u_hue;
uniform float u_dim;

varying vec2 vUv;

void main() {
  float along = vUv.x;  // 0..1 along curve
  float across = abs(vUv.y - 0.5) * 2.0; // 0..1 from center to edge

  // Edge softness — fade out at tube edges
  float edgeFade = 1.0 - smoothstep(0.3, 1.0, across);

  // Hue-shifted base colors: cyan (0.0) → orange (1.0)
  vec3 idleCyan = vec3(0.1, 0.15, 0.25);
  vec3 idleOrange = vec3(0.25, 0.15, 0.05);
  vec3 idleColor = mix(idleCyan, idleOrange, u_hue);

  vec3 energyCyan = vec3(0.0, 0.9, 1.0);
  vec3 energyOrange = vec3(0.9, 0.5, 0.1);
  vec3 energyColor = mix(energyCyan, energyOrange, u_hue);

  vec3 brightCyan = vec3(0.3, 0.95, 1.0);
  vec3 brightOrange = vec3(1.0, 0.7, 0.3);
  vec3 brightColor = mix(brightCyan, brightOrange, u_hue);

  vec3 color;
  float alpha;

  // State 0: Idle — faint wire
  if (u_state == 0) {
    color = idleColor;
    alpha = 0.15 * edgeFade;
  }

  // State 1: Surge — single bright bolt traveling along the edge
  else if (u_state == 1) {
    float dist = along - u_surge_progress;
    // Bright head
    float head = exp(-80.0 * dist * dist);
    // Exponential decay tail (behind the surge front)
    float tail = dist < 0.0 ? exp(8.0 * dist) * 0.4 : 0.0;
    float intensity = head + tail;
    // Color with white-hot core
    vec3 white = vec3(1.0, 1.0, 1.0);
    color = mix(energyColor, white, head * 0.7);
    alpha = intensity * edgeFade;
    // Keep dim base visible
    vec3 dimBase = mix(vec3(0.05, 0.12, 0.2), vec3(0.15, 0.1, 0.03), u_hue);
    color = mix(dimBase, color, clamp(intensity, 0.0, 1.0));
    alpha = max(alpha, 0.1 * edgeFade);
  }

  // State 2: Flow — repeating pulses at constant visual speed
  else if (u_state == 2) {
    float speed = 150.0 / max(u_curve_length, 1.0); // constant visual speed
    float phase = fract(along - u_time * speed);
    float pulse = exp(-12.0 * phase);
    float baseLine = 0.15;
    float intensity = baseLine + pulse * 0.85;
    color = mix(energyColor, brightColor, pulse);
    alpha = intensity * edgeFade;
  }

  // State 3: Completed — flash then settle to dim cyan/amber glow
  else if (u_state == 3) {
    vec3 flashCyan = vec3(0.5, 1.0, 1.0);
    vec3 flashAmber = vec3(1.0, 0.8, 0.4);
    vec3 flashColor = mix(flashCyan, flashAmber, u_hue);
    vec3 settledCyan = vec3(0.0, 0.5, 0.6);
    vec3 settledAmber = vec3(0.5, 0.3, 0.1);
    vec3 settledColor = mix(settledCyan, settledAmber, u_hue);
    color = mix(settledColor, flashColor, u_flash);
    float settledAlpha = 0.35;
    float flashAlpha = 0.9;
    alpha = mix(settledAlpha, flashAlpha, u_flash) * edgeFade;
  }

  // State 4: Failed — red pulse then dim red (always red regardless of hue)
  else if (u_state == 4) {
    vec3 flashColor = vec3(1.0, 0.3, 0.2);
    vec3 settledColor = vec3(0.5, 0.1, 0.08);
    color = mix(settledColor, flashColor, u_flash);
    float settledAlpha = 0.3;
    float flashAlpha = 0.9;
    alpha = mix(settledAlpha, flashAlpha, u_flash) * edgeFade;
  }

  // Fallback
  else {
    color = idleColor;
    alpha = 0.15 * edgeFade;
  }

  // Apply dim factor for sequencing-only edges
  alpha *= u_dim;

  gl_FragColor = vec4(color, alpha);
}
`;
