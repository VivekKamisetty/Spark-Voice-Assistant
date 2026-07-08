// The reactive orb: a noise-displaced sphere rendered with a custom GLSL
// shader (fresnel rim lighting + emissive glow), driven by state/amplitude
// messages from the backend. No bundler in this project, so this is plain
// CommonJS requiring three's prebuilt CJS bundle directly — deliberately
// avoids three/examples/jsm's postprocessing addons (ESM-only, no CJS
// build): dynamic import() of them was tried and reliably crashed the
// Electron renderer process outright the moment the import executed (not a
// catchable JS error) in this non-bundled nodeIntegration setup. Bloom is
// instead approximated with a plain additive-blended glow mesh — see the
// comment above GLOW_VERTEX_SHADER.
const THREE = require('three');

// Colors per state — same semantics as the old CSS bubble, plus a distinct
// color for awaiting_confirmation (new in Phase 3; nothing used it before).
// Hex strings kept alongside the THREE.Color versions (built from the same
// values just below) so renderer.js can tint the panel's top edge to match
// the orb's current state without hardcoding a second copy of these colors.
const STATE_COLOR_HEX = {
  idle: '#8a7fd6',
  calibrating: '#2196f3',
  listening: '#4caf50',
  thinking: '#2196f3',
  speaking: '#ffc107',
  awaiting_confirmation: '#ff5da2',
};
const STATE_COLORS = Object.fromEntries(
  Object.entries(STATE_COLOR_HEX).map(([state, hex]) => [state, new THREE.Color(hex)])
);

// Ashima Arts' classic 3D simplex noise (public domain / MIT), the standard
// reference implementation used across countless WebGL shaders.
const SIMPLEX_NOISE_GLSL = `
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
  const vec2 C = vec2(1.0/6.0, 1.0/3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);

  vec3 i  = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);

  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);

  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;

  i = mod289(i);
  vec4 p = permute(permute(permute(
            i.z + vec4(0.0, i1.z, i2.z, 1.0))
          + i.y + vec4(0.0, i1.y, i2.y, 1.0))
          + i.x + vec4(0.0, i1.x, i2.x, 1.0));

  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;

  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);

  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);

  vec4 x = x_ *ns.x + ns.yyyy;
  vec4 y = y_ *ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);

  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);

  vec4 s0 = floor(b0)*2.0 + 1.0;
  vec4 s1 = floor(b1)*2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));

  vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww;

  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);

  vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;

  vec4 m = max(0.5 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
  m = m * m;
  return 105.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
}
`;

// Outer membrane: bass sets overall scale, mids drive noise displacement
// amplitude, highs tighten the ripple frequency — three independent knobs
// instead of one flat amplitude, so the surface visibly reacts differently
// to a bassy "thump" vs a hissy "s" sound instead of just pulsing uniformly.
const VERTEX_SHADER = `
  uniform float uTime;
  uniform float uMid;
  uniform float uHigh;
  uniform float uSwirl;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  varying float vDisplacement;

  ${SIMPLEX_NOISE_GLSL}

  void main() {
    vNormal = normalize(normalMatrix * normal);

    // Slow ambient "breathing" noise always present, so the orb never looks
    // static/dead even at idle; a second, faster layer scales with live mid
    // energy, and highs tighten the spatial frequency for a tighter ripple.
    float rippleFreq = 2.2 + uHigh * 2.5;
    float slow = snoise(position * 1.5 + uTime * 0.15) * 0.06;
    float reactive = snoise(position * rippleFreq + uTime * 0.6 + uSwirl) * uMid * 0.9;
    float displacement = slow + reactive;
    vDisplacement = displacement;

    vec3 displaced = position + normal * displacement;
    vec4 mvPosition = modelViewMatrix * vec4(displaced, 1.0);
    vViewPosition = -mvPosition.xyz;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const FRAGMENT_SHADER = `
  uniform vec3 uColor;
  uniform float uGlow;
  uniform float uTime;
  uniform float uChroma;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  varying float vDisplacement;

  // Cheap analytic hue rotation (no HSV round-trip) — rotates RGB around the
  // luma axis by the angle param, in radians. Standard constant-matrix
  // technique, ~a dozen multiply-adds, negligible cost next to noise above.
  vec3 hueRotate(vec3 color, float angle) {
    float c = cos(angle);
    float s = sin(angle);
    mat3 rot = mat3(
      0.299 + 0.701 * c + 0.168 * s,  0.587 - 0.587 * c + 0.330 * s,  0.114 - 0.114 * c - 0.497 * s,
      0.299 - 0.299 * c - 0.328 * s,  0.587 + 0.413 * c + 0.035 * s,  0.114 - 0.114 * c + 0.292 * s,
      0.299 - 0.300 * c + 1.250 * s,  0.587 - 0.588 * c - 1.050 * s,  0.114 + 0.886 * c - 0.203 * s
    );
    return clamp(rot * color, 0.0, 1.0);
  }

  void main() {
    vec3 viewDir = normalize(vViewPosition);
    // Fresnel term: near-zero facing the camera, near-1 at grazing angles —
    // the classic glowing-rim look. Sampled at three slightly different
    // exponents per channel below to fake chromatic aberration at the rim
    // without a texture/UV-offset (there's no texture here to offset).
    // Lower exponents than a typical fresnel rim (was 2.0/2.2/2.45) — at
    // that steepness only a thin sliver right at the silhouette is bright,
    // and with the inner core mesh now sitting inside it, that thin sliver
    // reads as "mostly dark body with a faint edge" rather than a rim that
    // clearly dominates the surface.
    float fresnelBase = max(dot(viewDir, vNormal), 0.0);
    float fresnelR = pow(1.0 - fresnelBase, 1.1);
    float fresnelG = pow(1.0 - fresnelBase, 1.3);
    float fresnelB = pow(1.0 - fresnelBase, 1.5);
    float fresnel = fresnelG;

    // Subtle iridescent sheen: a slow hue drift, strongest at the rim, so
    // the surface catches a faint shifting sheen without fighting the base
    // state color's overall hue identity (kept to a small angle range).
    float iridAngle = sin(uTime * 0.35 + vDisplacement * 3.0) * 0.35 * fresnel;
    vec3 iridColor = hueRotate(uColor, iridAngle);

    // Was 0.55 -- with the wider fresnel band above, the face-on area covers
    // more of the visible disc, so it needs to be less dark itself or the
    // rim brightening reads as a small highlight rather than the dominant
    // "glowing membrane" look.
    vec3 core = iridColor * 0.75;
    vec3 rimR = iridColor + vec3(0.4 + uChroma, 0.4, 0.4);
    vec3 rimG = iridColor + vec3(0.4, 0.4 + uChroma * 0.4, 0.4);
    vec3 rimB = iridColor + vec3(0.4, 0.4, 0.4 + uChroma);

    vec3 color;
    color.r = mix(core.r, rimR.r, fresnelR) * uGlow;
    color.g = mix(core.g, rimG.g, fresnelG) * uGlow;
    color.b = mix(core.b, rimB.b, fresnelB) * uGlow;

    // Slightly transparent toward the center (low fresnel) so the brighter,
    // lagged inner core mesh can be glimpsed through the membrane, more
    // opaque toward the rim to keep the silhouette solid and readable.
    float alpha = mix(0.72, 1.0, fresnel);

    gl_FragColor = vec4(color, alpha);
  }
`;

// Inner core: a smaller, brighter, simpler-shaded mesh whose motion uses a
// time- and easing-lagged copy of the outer membrane's own uniforms, so it
// visibly "catches up" to the outer shell's deformation a beat later —
// reads as viscous/jelly-like depth rather than a rigid solid ball.
const CORE_VERTEX_SHADER = `
  uniform float uTime;
  uniform float uMid;
  uniform float uHigh;
  varying float vDisplacement;

  ${SIMPLEX_NOISE_GLSL}

  void main() {
    float rippleFreq = 2.0 + uHigh * 2.0;
    float displacement = snoise(position * rippleFreq + uTime * 0.6) * uMid * 0.7;
    vDisplacement = displacement;
    vec3 displaced = position + normal * displacement;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(displaced, 1.0);
  }
`;

const CORE_FRAGMENT_SHADER = `
  uniform vec3 uColor;
  uniform float uGlow;
  varying float vDisplacement;

  void main() {
    vec3 color = (uColor + vec3(0.5)) * uGlow;
    gl_FragColor = vec4(color, 1.0);
  }
`;

// Bloom approximation WITHOUT three's postprocessing addons: a larger,
// backside-rendered sphere with an additive fresnel glow — the classic
// cheap "atmosphere shell" technique (one extra low-poly mesh, one extra
// draw call, no render targets/extra passes). A real EffectComposer +
// UnrealBloomPass pipeline was tried first, loaded via dynamic import()
// since those addons are ESM-only with no CJS build — that reliably
// hard-crashed the Electron renderer process outright (not a catchable JS
// error, the process itself died) the moment the import executed, in this
// non-bundled nodeIntegration setup. This avoids that failure mode entirely,
// and costs less GPU than a real bloom pass would have anyway.
const GLOW_VERTEX_SHADER = `
  varying vec3 vNormal;
  varying vec3 vViewPosition;

  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vViewPosition = -mvPosition.xyz;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const GLOW_FRAGMENT_SHADER = `
  uniform vec3 uColor;
  uniform float uIntensity;
  varying vec3 vNormal;
  varying vec3 vViewPosition;

  void main() {
    vec3 viewDir = normalize(vViewPosition);
    float fresnel = pow(1.0 - max(dot(viewDir, vNormal), 0.0), 3.0);
    gl_FragColor = vec4(uColor, fresnel * uIntensity);
  }
`;

function createOrb(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  const pixelRatio = Math.min(window.devicePixelRatio, 2);
  renderer.setPixelRatio(pixelRatio);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.z = 3.2;

  const geometry = new THREE.IcosahedronGeometry(1, 24);
  const uniforms = {
    uTime: { value: 0 },
    uMid: { value: 0 },
    uHigh: { value: 0 },
    uSwirl: { value: 0 },
    uColor: { value: STATE_COLORS.idle.clone() },
    uGlow: { value: 1.0 },
    uChroma: { value: 0.0 },
  };
  const material = new THREE.ShaderMaterial({
    vertexShader: VERTEX_SHADER,
    fragmentShader: FRAGMENT_SHADER,
    uniforms,
    transparent: true,
  });
  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);

  // Smaller, lower-poly core — mostly hidden inside the outer membrane, only
  // glimpsed through its semi-transparent center, so it doesn't need the
  // outer shell's vertex density.
  const coreGeometry = new THREE.IcosahedronGeometry(0.62, 8);
  const coreUniforms = {
    uTime: { value: 0 },
    uMid: { value: 0 },
    uHigh: { value: 0 },
    uColor: { value: STATE_COLORS.idle.clone() },
    uGlow: { value: 1.0 },
  };
  const coreMaterial = new THREE.ShaderMaterial({
    vertexShader: CORE_VERTEX_SHADER,
    fragmentShader: CORE_FRAGMENT_SHADER,
    uniforms: coreUniforms,
  });
  const coreMesh = new THREE.Mesh(coreGeometry, coreMaterial);
  scene.add(coreMesh);

  // Bloom-like glow shell: bigger than the membrane, back faces only (so the
  // fresnel term reads as a soft halo hugging the silhouette rather than a
  // solid ball), additive blending, no depth write so it never occludes
  // anything. See the comment above GLOW_VERTEX_SHADER for why this exists
  // instead of a real postprocessing bloom pass.
  // Radius kept comfortably under the camera's visible frustum extent at the
  // origin's depth (~1.32 at this FOV/distance, including scale pulsing up
  // to 1.15x) — anything bigger gets clipped by the canvas's square edges,
  // which reads as a distracting octagonal cutoff around the round glow.
  const glowGeometry = new THREE.IcosahedronGeometry(1.05, 6);
  const glowUniforms = {
    uColor: { value: STATE_COLORS.idle.clone() },
    uIntensity: { value: 0.6 },
  };
  const glowMaterial = new THREE.ShaderMaterial({
    vertexShader: GLOW_VERTEX_SHADER,
    fragmentShader: GLOW_FRAGMENT_SHADER,
    uniforms: glowUniforms,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.BackSide,
  });
  const glowMesh = new THREE.Mesh(glowGeometry, glowMaterial);
  scene.add(glowMesh);

  function resize() {
    const { clientWidth, clientHeight } = canvas;
    renderer.setSize(clientWidth, clientHeight, false);
    camera.aspect = clientWidth / clientHeight || 1;
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize);
  resize();

  // Smoothly-lerped targets — the spec explicitly calls for never snapping
  // color/amplitude between messages, so every incoming value is a target
  // the render loop eases toward rather than an instant jump.
  let targetBass = 0;
  let targetMid = 0;
  let targetHigh = 0;
  let displayBass = 0;
  let displayMid = 0;
  let displayHigh = 0;
  // The core's own, more slowly-eased copies — lagging behind the outer
  // membrane's values is what reads as "catching up" a beat later.
  let coreBass = 0;
  let coreMid = 0;
  let coreHigh = 0;
  let targetColor = STATE_COLORS.idle.clone();
  let currentState = 'idle';

  function setState(state) {
    currentState = state;
    targetColor = (STATE_COLORS[state] || STATE_COLORS.idle).clone();
  }

  function setAmplitude(bass, mid, high) {
    targetBass = Math.min(bass, 1.4);
    targetMid = Math.min(mid, 1.4);
    targetHigh = Math.min(high, 1.4);
  }

  const clock = new THREE.Clock();
  const CORE_TIME_LAG = 0.18; // seconds the core's noise sampling trails the outer shell by

  function animate() {
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();

    const isActive = currentState === 'listening' || currentState === 'speaking';
    const decayedBass = isActive ? targetBass : 0;
    const decayedMid = isActive ? targetMid : 0;
    const decayedHigh = isActive ? targetHigh : 0;

    displayBass += (decayedBass - displayBass) * 0.15;
    displayMid += (decayedMid - displayMid) * 0.15;
    displayHigh += (decayedHigh - displayHigh) * 0.15;
    // Slower easing than the outer shell's, on top of the fixed time offset
    // in its shader — two independent sources of lag compound into a
    // clearly-visible "following" motion rather than a subtle one.
    coreBass += (decayedBass - coreBass) * 0.05;
    coreMid += (decayedMid - coreMid) * 0.05;
    coreHigh += (decayedHigh - coreHigh) * 0.05;

    uniforms.uTime.value = t;
    uniforms.uMid.value = displayMid;
    uniforms.uHigh.value = displayHigh;
    uniforms.uSwirl.value = currentState === 'thinking' ? t * 0.8 : 0;
    uniforms.uColor.value.lerp(targetColor, 0.08);
    uniforms.uGlow.value = 1.0 + displayBass * 0.6;
    uniforms.uChroma.value = displayHigh * 0.25;
    mesh.scale.setScalar(1.0 + displayBass * 0.12);

    coreUniforms.uTime.value = Math.max(0, t - CORE_TIME_LAG);
    coreUniforms.uMid.value = coreMid;
    coreUniforms.uHigh.value = coreHigh;
    coreUniforms.uColor.value.copy(uniforms.uColor.value);
    coreUniforms.uGlow.value = 1.4 + coreBass * 0.5;
    coreMesh.scale.setScalar(1.0 + coreBass * 0.12);

    glowUniforms.uColor.value.copy(uniforms.uColor.value);
    glowUniforms.uIntensity.value = 0.45 + displayBass * 0.5;
    glowMesh.scale.setScalar(1.0 + displayBass * 0.15);

    mesh.rotation.y = t * 0.05;
    coreMesh.rotation.y = Math.max(0, t - CORE_TIME_LAG) * 0.05;
    if (currentState === 'thinking') {
      mesh.rotation.y += Math.sin(t * 1.5) * 0.1;
    }

    renderer.render(scene, camera);
  }
  animate();

  return { setState, setAmplitude };
}

module.exports = { createOrb, STATE_COLOR_HEX };
