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
uniform float u_pulse_count;
uniform float u_flow_age;

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

  // Absolute black base — critical for screen blend mode.
  // Any non-black pixel will be visible via CSS screen compositing.
  vec3 color = vec3(0.0);
  float alpha = 1.0; // alpha irrelevant for screen blend over black

  // State 0: Idle — absolute black (invisible via screen blend)
  if (u_state == 0) {
    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
    return;
  }

  // State 1: Surge + flow combined — the surge bolt is the leading wavefront,
  // flow pulses spawn from source behind it
  else if (u_state == 1) {
    float period = 1.5;
    float speed = 1.0 / period;
    float pc = max(u_pulse_count, 1.0);

    // Surge bolt (bright leading edge)
    float dist = along - u_surge_progress;
    float head = exp(-80.0 * dist * dist);
    float tail = dist < 0.0 ? exp(8.0 * dist) * 0.4 : 0.0;
    float surgeIntensity = head + tail;
    vec3 white = vec3(1.0, 1.0, 1.0);
    vec3 surgeColor = mix(vec3(0.0), mix(energyColor, white, head * 0.7), surgeIntensity);

    // Flow pulses behind the surge front
    float phase = fract(along * pc - u_time * speed);
    float pulse = exp(-5.0 * (1.0 - phase));
    vec3 flowColor = mix(vec3(0.0), mix(energyColor, brightColor, pulse * pulse), pulse);

    // Only show flow pulses behind the surge wavefront
    float behindSurge = 1.0 - smoothstep(u_surge_progress - 0.05, u_surge_progress, along);
    flowColor *= behindSurge;

    // Combine: surge on top, flow behind
    color = max(surgeColor, flowColor) * edgeFade;
  }

  // State 2: Flow — continuous synchronized pulses (surge is done)
  else if (u_state == 2) {
    float period = 1.5;
    float speed = 1.0 / period;
    float pc = max(u_pulse_count, 1.0);
    float phase = fract(along * pc - u_time * speed);
    float pulse = exp(-5.0 * (1.0 - phase));

    color = mix(vec3(0.0), mix(energyColor, brightColor, pulse * pulse), pulse) * edgeFade;
  }

  // State 3: Completed — flash from black
  else if (u_state == 3) {
    vec3 flashCyan = vec3(0.5, 1.0, 1.0);
    vec3 flashAmber = vec3(1.0, 0.8, 0.4);
    vec3 flashColor = mix(flashCyan, flashAmber, u_hue);
    color = flashColor * u_flash * 0.9 * edgeFade;
  }

  // State 4: Failed — red flash from black
  else if (u_state == 4) {
    color = vec3(1.0, 0.3, 0.2) * u_flash * 0.9 * edgeFade;
  }

  // Apply dim factor for sequencing-only edges
  color *= u_dim;

  gl_FragColor = vec4(color, 1.0);
}
`;
