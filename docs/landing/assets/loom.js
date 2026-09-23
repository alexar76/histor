/** HISTOR hero — the log itself, drawn as what it is.
 *
 *  registry galaxy   every endpoint in the official MCP registry, as a slowly turning spiral of
 *                    points on the floor; the bright ones are endpoints whose tools are on record
 *  observation       an arc of light from one endpoint to a new leaf
 *  leaves            labels, one crystal per leaf, coloured by method and verdict
 *  current block     the rightmost 32-leaf subtree, filling left to right; energy runs up its edges
 *  hash comet        a leaf's hash climbing to the block root, with its trail
 *  history column    every finished 32-leaf block, frozen into a crystal on a helix behind the seal —
 *                    RFC 9162 stores exactly these complete subtrees and never recomputes them
 *  seal              the log root inside two holographic rings: the real root hash, and the signed
 *                    tree head it belongs to. When a head is signed, the hash re-writes itself and a
 *                    shockwave runs across the registry
 *  change            an amber leaf and an amber ripple on the galaxy where that endpoint lives
 *
 * LIVE when /api/v1/log answers with entries: leaf colours are the real labels, the column is the
 * real number of finished blocks, the rings carry the real root and tree size. SIM when the log is
 * empty or unreachable — badged as such, because a model of a log is not a log.
 */
import * as THREE from "./vendor/three/three.module.min.js";
import { OrbitControls } from "./vendor/three/addons/controls/OrbitControls.js";
import { EffectComposer } from "./vendor/three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "./vendor/three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "./vendor/three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "./vendor/three/addons/postprocessing/OutputPass.js";
import { ShaderPass } from "./vendor/three/addons/postprocessing/ShaderPass.js";

export const BLOCK = 32;
const LEVELS = 5; // log2(BLOCK)
const SPACING = 0.52;
const LEVEL_H = 1.05;
const FLOOR_Y = -2.2;

export const PALETTE = {
  observation: 0x5fe3ff,
  "pattern-scan": 0xb18cff,
  "name-threat": 0x8fa8cc,
  pass: 0x56f0a8,
  fail: 0xffb547,
  inconclusive: 0x6f7c91,
  node: 0x9fd8ff,
  root: 0xf3e6c8,
  seal: 0xffd58a,
  edge: 0x3f86c8,
};

/** Colour of a log entry, from its short method name and verdict. */
export function leafColor(entry) {
  if (!entry) return PALETTE.inconclusive;
  const m = entry.methodShort || entry.method || "";
  if (m === "continuity") return PALETTE[entry.verdict] ?? PALETTE.inconclusive;
  if (m === "observation" && entry.verdict === "inconclusive") return PALETTE.inconclusive;
  return PALETTE[m] ?? PALETTE.node;
}

export async function probeLog(api) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 6000);
  const get = (path) => fetch(`${api}${path}`, { signal: ctrl.signal, headers: { accept: "application/json" } });
  try {
    const sthRes = await get("/api/v1/log/sth");
    if (sthRes.status === 404) return { mode: "EMPTY", sth: null, entries: [], stats: null };
    if (!sthRes.ok) return { mode: "UNREACHABLE", sth: null, entries: [], stats: null };
    const sth = await sthRes.json();
    const size = Number(sth.treeSize) || 0;
    const start = Math.floor(Math.max(0, size - 1) / BLOCK) * BLOCK;
    const [res, statsRes] = await Promise.all([get(`/api/v1/log/entries?start=${start}&end=${size}`), get("/api/v1/stats")]);
    if (!res.ok) return { mode: "UNREACHABLE", sth: null, entries: [], stats: null };
    const data = await res.json();
    const stats = statsRes.ok ? await statsRes.json() : null;
    return { mode: size ? "LIVE" : "EMPTY", sth, entries: Array.isArray(data.entries) ? data.entries : [], stats };
  } catch {
    return { mode: "UNREACHABLE", sth: null, entries: [], stats: null };
  } finally {
    clearTimeout(timer);
  }
}

function simEntry(i) {
  // A plausible rhythm: continuity heartbeats dominate, a new server brings three labels, and
  // now and then a set changes. Deterministic, so the model does not flicker per reload.
  const x = Math.abs((Math.sin(i * 12.9898) * 43758.5453) % 1);
  if (x < 0.07) return { methodShort: "continuity", verdict: "fail" };
  if (x < 0.6) return { methodShort: "continuity", verdict: "pass" };
  if (x < 0.75) return { methodShort: "observation", verdict: "pass" };
  if (x < 0.88) return { methodShort: "pattern-scan", verdict: x < 0.8 ? "inconclusive" : "pass" };
  return { methodShort: "name-threat", verdict: "pass" };
}

function simHash(n) {
  let h = "";
  for (let i = 0; i < 64; i++) h += "0123456789abcdef"[Math.floor(Math.abs(Math.sin((n + 1) * 91.7 + i * 7.13) * 1e4)) % 16];
  return h;
}

// ------------------------------------------------------------------------------------------
// shaders
// ------------------------------------------------------------------------------------------
const GALAXY_VS = /* glsl */ `
  attribute float aSeed; attribute float aKind; attribute float aSize;
  uniform float uTime; uniform float uPixel;
  varying float vKind; varying float vTw;
  void main() {
    vKind = aKind;
    vTw = 0.55 + 0.45 * sin(uTime * (1.2 + aSeed * 2.0) + aSeed * 60.0);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * uPixel * (0.6 + 0.6 * vTw) * (26.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }`;
const GALAXY_FS = /* glsl */ `
  varying float vKind; varying float vTw;
  void main() {
    vec2 c = gl_PointCoord - 0.5; float d = length(c);
    if (d > 0.5) discard;
    float core = smoothstep(0.5, 0.0, d);
    vec3 seen = vec3(0.37, 0.89, 1.0);
    vec3 dim = vec3(0.22, 0.25, 0.62);
    vec3 col = mix(dim, seen, vKind);
    gl_FragColor = vec4(col * (0.6 + 1.4 * core * vTw), core * (0.25 + 0.75 * vKind) * vTw);
  }`;
const EDGE_VS = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const EDGE_FS = /* glsl */ `
  uniform float uTime; uniform float uHeat; uniform vec3 uColor; uniform vec3 uHot;
  varying vec2 vUv;
  void main() {
    float flow = fract(vUv.x * 2.5 - uTime * 0.55);
    float band = smoothstep(0.0, 0.12, flow) * smoothstep(0.45, 0.12, flow);
    vec3 col = uColor * (0.18 + 0.55 * band) + uHot * uHeat * 2.2;
    float a = 0.22 + 0.45 * band + uHeat * 0.9;
    gl_FragColor = vec4(col, a);
  }`;
const ARC_FS = /* glsl */ `
  uniform float uTime; uniform float uHeat; uniform vec3 uColor; varying vec2 vUv;
  void main() {
    float head = smoothstep(uTime - 0.25, uTime, vUv.x) * step(vUv.x, uTime);
    float tail = smoothstep(uTime - 0.9, uTime, vUv.x) * step(vUv.x, uTime) * 0.35;
    float a = (head + tail) * uHeat;
    gl_FragColor = vec4(uColor * (1.2 + head * 2.5), a);
  }`;
const TRAIL_VS = /* glsl */ `
  attribute float aAge; uniform float uPixel; varying float vAge;
  void main() {
    vAge = aAge;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = (1.0 - aAge) * 9.0 * uPixel * (18.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }`;
const TRAIL_FS = /* glsl */ `
  uniform vec3 uColor; varying float vAge;
  void main() {
    float d = length(gl_PointCoord - 0.5); if (d > 0.5) discard;
    gl_FragColor = vec4(uColor * 2.0, smoothstep(0.5, 0.0, d) * (1.0 - vAge));
  }`;
const NEBULA_FS = /* glsl */ `
  varying vec3 vDir; uniform float uTime;
  float hash(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
  float noise(vec3 x) {
    vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
  }
  float fbm(vec3 p) { float v = 0.0; float a = 0.5; for (int i = 0; i < 5; i++) { v += a * noise(p); p *= 2.03; a *= 0.5; } return v; }
  void main() {
    vec3 d = normalize(vDir);
    float n = fbm(d * 2.4 + vec3(0.0, uTime * 0.01, 0.0));
    float m = fbm(d * 5.0 - vec3(uTime * 0.008));
    vec3 base = vec3(0.012, 0.02, 0.04);
    vec3 teal = vec3(0.02, 0.16, 0.22) * smoothstep(0.45, 0.85, n);
    vec3 violet = vec3(0.12, 0.05, 0.2) * smoothstep(0.55, 0.9, m) * (0.5 + 0.5 * d.y);
    vec3 amber = vec3(0.18, 0.11, 0.03) * smoothstep(0.7, 0.95, n * m * 1.6) * max(0.0, -d.x);
    gl_FragColor = vec4(base + teal + violet + amber, 1.0);
  }`;
const FINAL_PASS = {
  uniforms: { tDiffuse: { value: null }, uTime: { value: 0 }, uRes: { value: new THREE.Vector2(1, 1) } },
  vertexShader: /* glsl */ `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse; uniform float uTime; uniform vec2 uRes; varying vec2 vUv;
    void main() {
      vec2 d = vUv - 0.5; float r = dot(d, d);
      vec2 off = d * 0.004 * (0.4 + r * 2.5);
      vec3 col = vec3(texture2D(tDiffuse, vUv + off).r, texture2D(tDiffuse, vUv).g, texture2D(tDiffuse, vUv - off).b);
      col *= mix(1.0, 0.32, smoothstep(0.08, 0.62, r * 1.5));
      float g = fract(sin(dot(vUv * uRes + fract(uTime) * 91.0, vec2(12.9898, 78.233))) * 43758.5453);
      col += (g - 0.5) * 0.028;
      gl_FragColor = vec4(col, 1.0);
    }`,
};

function glowSprite(color, size) {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const ctx = c.getContext("2d");
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.25, "rgba(255,255,255,0.45)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending }));
  s.scale.setScalar(size);
  return s;
}

// ------------------------------------------------------------------------------------------
// the scene
// ------------------------------------------------------------------------------------------
export function mountLoom(canvas, { onPhase = () => {}, reducedMotion = false } = {}) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "high-performance" });
  } catch {
    return null;
  }
  const small = Math.min(window.innerWidth, window.innerHeight) < 700;
  const pixel = Math.min(window.devicePixelRatio || 1, small ? 1.5 : 2);
  renderer.setPixelRatio(pixel);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x04070c, 0.02);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 400);

  // Framing: the tree spans x ≈ -7.4…9, the seal floats at y ≈ 8.9. fit() keeps that in frame for
  // any aspect ratio until the reader takes the camera; the galaxy is context around it.
  const SCENE_CENTER = new THREE.Vector3(0.4, 3.9, 0);
  const SCENE_HALF_W = 9.2;
  const SCENE_HALF_H = 7.0;
  const VIEW_DIR = new THREE.Vector3(-0.2, 0.26, 1).normalize();
  let userMoved = false;

  const controls = new OrbitControls(camera, canvas);
  controls.target.copy(SCENE_CENTER);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.maxPolarAngle = Math.PI * 0.58;
  controls.autoRotate = !reducedMotion;
  controls.autoRotateSpeed = 0.28;
  controls.enablePan = false;
  // No wheel zoom: over a hero canvas the wheel belongs to the page, or the reader gets stuck.
  controls.enableZoom = false;
  controls.addEventListener("start", () => { userMoved = true; });

  function fit() {
    if (userMoved) return;
    const vfov = THREE.MathUtils.degToRad(camera.fov);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    const dist = Math.max(SCENE_HALF_W / Math.tan(hfov / 2), SCENE_HALF_H / Math.tan(vfov / 2)) * 1.02;
    camera.position.copy(SCENE_CENTER).addScaledVector(VIEW_DIR, Math.min(dist, 60));
    controls.update();
  }

  // Sky: a noise nebula on the inside of a sphere, plus drifting dust.
  const nebulaMat = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { uTime: { value: 0 } },
    vertexShader: "varying vec3 vDir; void main(){ vDir = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
    fragmentShader: NEBULA_FS,
  });
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(160, 48, 32), nebulaMat));
  {
    const n = small ? 700 : 1600;
    const dust = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const r = 40 + Math.random() * 90;
      const th = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      dust.set([r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph) * 0.6 + 10, r * Math.sin(ph) * Math.sin(th)], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(dust, 3));
    scene.add(new THREE.Points(g, new THREE.PointsMaterial({ color: 0xbfd6ff, size: 0.35, sizeAttenuation: true, transparent: true,
      opacity: 0.55, depthWrite: false, fog: false })));
  }

  scene.add(new THREE.AmbientLight(0x6f86a8, 0.5));
  const key = new THREE.PointLight(0xfff1d6, 90, 70, 1.6);
  key.position.set(4, 14, 9);
  scene.add(key);
  const rim = new THREE.PointLight(0x5fe3ff, 50, 60, 1.7);
  rim.position.set(-11, 4, -7);
  scene.add(rim);
  const warm = new THREE.PointLight(0xffb547, 30, 40, 1.8);
  warm.position.set(6, 2, -6);
  scene.add(warm);

  // Registry galaxy.
  const GALAXY_N = small ? 1800 : 4200;
  const galaxy = new THREE.Group();
  galaxy.position.y = FLOOR_Y;
  scene.add(galaxy);
  const gPos = new Float32Array(GALAXY_N * 3);
  const gSeed = new Float32Array(GALAXY_N);
  const gKind = new Float32Array(GALAXY_N);
  const gSize = new Float32Array(GALAXY_N);
  for (let i = 0; i < GALAXY_N; i++) {
    const arm = i % 3;
    const t = Math.pow(Math.random(), 0.7);
    const r = 5.5 + t * 16;
    const a = arm * (Math.PI * 2 / 3) + r * 0.33 + (Math.random() - 0.5) * (0.5 + t * 0.6);
    const spread = (Math.random() - 0.5) * (0.6 + t * 1.8);
    gPos.set([Math.cos(a) * r + spread, (Math.random() - 0.5) * 0.35 * (1 - t * 0.5), Math.sin(a) * r + spread], i * 3);
    gSeed[i] = Math.random();
    gSize[i] = 0.7 + Math.random() * 1.6;
  }
  const galaxyGeo = new THREE.BufferGeometry();
  galaxyGeo.setAttribute("position", new THREE.BufferAttribute(gPos, 3));
  galaxyGeo.setAttribute("aSeed", new THREE.BufferAttribute(gSeed, 1));
  galaxyGeo.setAttribute("aKind", new THREE.BufferAttribute(gKind, 1));
  galaxyGeo.setAttribute("aSize", new THREE.BufferAttribute(gSize, 1));
  const galaxyMat = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uPixel: { value: pixel } },
    vertexShader: GALAXY_VS, fragmentShader: GALAXY_FS,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  galaxy.add(new THREE.Points(galaxyGeo, galaxyMat));
  const galaxyCore = glowSprite(0x2e6fa8, 7);
  galaxyCore.material.opacity = 0.3;
  galaxyCore.position.y = 0.2;
  galaxy.add(galaxyCore);
  function setObservedRatio(ratio) {
    const r = Math.max(0.02, Math.min(1, ratio));
    for (let i = 0; i < GALAXY_N; i++) gKind[i] = gSeed[(i * 7919) % GALAXY_N] < r ? 1 : 0;
    galaxyGeo.attributes.aKind.needsUpdate = true;
  }
  setObservedRatio(0.42);
  function galaxyPoint(observedOnly = true) {
    for (let tries = 0; tries < 40; tries++) {
      const i = Math.floor(Math.random() * GALAXY_N);
      if (!observedOnly || gKind[i] > 0.5) {
        const p = new THREE.Vector3(gPos[i * 3], gPos[i * 3 + 1], gPos[i * 3 + 2]);
        return p.applyAxisAngle(new THREE.Vector3(0, 1, 0), galaxy.rotation.y);
      }
    }
    return new THREE.Vector3(gPos[0], gPos[1], gPos[2]);
  }

  // The current block.
  const tree = new THREE.Group();
  tree.position.set(0.8, 0, 0);
  scene.add(tree);
  const pos = [];
  for (let l = 0; l <= LEVELS; l++) {
    pos.push([]);
    const count = BLOCK >> l;
    for (let i = 0; i < count; i++) {
      const span = (1 << l) * SPACING;
      const x = -((BLOCK - 1) * SPACING) / 2 + i * span + (span - SPACING) / 2;
      pos[l].push(new THREE.Vector3(x, l * LEVEL_H + (l === 0 ? -0.018 * x * x : 0), l === 0 ? Math.sin(i * 0.9) * 0.18 : 0));
    }
  }
  const crystalGeo = new THREE.OctahedronGeometry(0.19, 0);
  crystalGeo.scale(1, 1.7, 1);
  const slotGeo = new THREE.OctahedronGeometry(0.1, 0);
  const slotMat = new THREE.MeshBasicMaterial({ color: 0x1a2a3c, transparent: true, opacity: 0.6 });
  const leaves = [];
  for (let i = 0; i < BLOCK; i++) {
    const slot = new THREE.Mesh(slotGeo, slotMat);
    slot.position.copy(pos[0][i]);
    tree.add(slot);
    const mat = new THREE.MeshPhysicalMaterial({
      color: 0x0c1420, emissive: PALETTE.node, emissiveIntensity: 0, roughness: 0.08, metalness: 0.05,
      transmission: 0.55, thickness: 0.7, ior: 1.45, iridescence: 1, iridescenceIOR: 1.35, clearcoat: 1,
      clearcoatRoughness: 0.05, transparent: true,
    });
    const leaf = new THREE.Mesh(crystalGeo, mat);
    leaf.position.copy(pos[0][i]);
    leaf.scale.setScalar(0.001);
    tree.add(leaf);
    const halo = glowSprite(PALETTE.node, 0.9);
    halo.position.copy(pos[0][i]);
    halo.material.opacity = 0;
    tree.add(halo);
    leaves.push({ mesh: leaf, halo, filled: false, grow: 0 });
  }
  const innerGeo = new THREE.IcosahedronGeometry(0.14, 0);
  const blockRootGeo = new THREE.DodecahedronGeometry(0.34, 0);
  const inner = [];
  for (let l = 1; l <= LEVELS; l++) {
    inner.push([]);
    pos[l].forEach((p) => {
      const mat = new THREE.MeshPhysicalMaterial({
        color: 0x0e1a28, emissive: l === LEVELS ? PALETTE.root : PALETTE.node, emissiveIntensity: 0.1,
        roughness: 0.12, metalness: 0.2, iridescence: 0.8, clearcoat: 1, flatShading: true,
      });
      const mesh = new THREE.Mesh(l === LEVELS ? blockRootGeo : innerGeo, mat);
      mesh.position.copy(p);
      tree.add(mesh);
      inner[l - 1].push({ mesh, glow: 0 });
    });
  }
  // Energy edges: one tube per child→parent, each with its own heat.
  const edges = [];
  for (let l = 0; l < LEVELS; l++) {
    pos[l].forEach((p, i) => {
      const q = pos[l + 1][i >> 1];
      const mid = p.clone().lerp(q, 0.5).add(new THREE.Vector3(0, 0.08, 0.1));
      const curve = new THREE.QuadraticBezierCurve3(p, mid, q);
      const mat = new THREE.ShaderMaterial({
        uniforms: { uTime: { value: 0 }, uHeat: { value: 0 }, uColor: { value: new THREE.Color(PALETTE.edge) },
          uHot: { value: new THREE.Color(PALETTE.observation) } },
        vertexShader: EDGE_VS, fragmentShader: EDGE_FS,
        transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      });
      tree.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 16, l === LEVELS - 1 ? 0.022 : 0.014, 6, false), mat));
      edges.push({ level: l, index: i, mat, curve });
    });
  }
  const edgeAt = (level, index) => edges.find((e) => e.level === level && e.index === index);

  // The seal: log root + two holographic rings + shockwave.
  const ROOT_POS = new THREE.Vector3(-2.2, LEVELS * LEVEL_H + 2.9, 0);
  const seal = new THREE.Group();
  seal.position.copy(ROOT_POS);
  scene.add(seal);
  const rootMat = new THREE.MeshPhysicalMaterial({
    color: 0x2a2010, emissive: PALETTE.root, emissiveIntensity: 0.5, roughness: 0.05, metalness: 0.3,
    transmission: 0.35, thickness: 1.2, iridescence: 1, iridescenceIOR: 1.6, clearcoat: 1, flatShading: true,
  });
  const logRoot = new THREE.Mesh(new THREE.IcosahedronGeometry(0.66, 0), rootMat);
  seal.add(logRoot);
  const coreGlow = glowSprite(0xffe7b8, 1.7);
  seal.add(coreGlow);

  function textBand(radius, height, fontPx, color) {
    const c = document.createElement("canvas");
    c.width = 4096;
    c.height = Math.round(fontPx * 1.6);
    const ctx = c.getContext("2d");
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 4;
    const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, side: THREE.DoubleSide, depthWrite: false,
      blending: THREE.AdditiveBlending, color });
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, height, 160, 1, true), mat);
    return {
      mesh,
      draw(text) {
        ctx.clearRect(0, 0, c.width, c.height);
        ctx.font = `500 ${fontPx}px "IBM Plex Mono", ui-monospace, monospace`;
        ctx.textBaseline = "middle";
        ctx.fillStyle = "#ffffff";
        const unit = `${text}   ◆   `;
        const w = ctx.measureText(unit).width;
        const reps = Math.max(1, Math.floor(c.width / w));
        const spacing = c.width / reps;
        for (let k = 0; k < reps; k++) ctx.fillText(unit, k * spacing, c.height / 2);
        tex.needsUpdate = true;
      },
    };
  }
  const hashBand = textBand(1.45, 0.22, 64, 0xffd58a);
  hashBand.mesh.rotation.x = 0.32;
  seal.add(hashBand.mesh);
  const sthBand = textBand(1.95, 0.16, 46, 0x9fe8ff);
  sthBand.mesh.rotation.x = -0.42;
  sthBand.mesh.rotation.z = 0.18;
  seal.add(sthBand.mesh);
  const tickRing = new THREE.Mesh(new THREE.TorusGeometry(1.16, 0.012, 6, 180),
    new THREE.MeshBasicMaterial({ color: PALETTE.seal, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending }));
  tickRing.rotation.x = Math.PI / 2;
  seal.add(tickRing);

  let rootHash = simHash(0);
  let scramble = 0;
  hashBand.draw(rootHash);
  sthBand.draw("SIGNED TREE HEAD · histor.sth/v1 · model");

  const shockMat = new THREE.MeshBasicMaterial({ color: PALETTE.seal, transparent: true, opacity: 0, side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending, depthWrite: false });
  const shock = new THREE.Mesh(new THREE.RingGeometry(0.96, 1.0, 128), shockMat);
  shock.rotation.x = -Math.PI / 2;
  shock.position.set(ROOT_POS.x, FLOOR_Y + 0.05, 0);
  scene.add(shock);
  let shockT = 1;
  const ripples = [];
  function ripple(at, color) {
    const m = new THREE.Mesh(new THREE.RingGeometry(0.9, 1.0, 64), new THREE.MeshBasicMaterial({ color, transparent: true,
      opacity: 0.9, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }));
    m.rotation.x = -Math.PI / 2;
    m.position.copy(at).add(new THREE.Vector3(0, FLOOR_Y + 0.06, 0));
    scene.add(m);
    ripples.push({ m, t: 0 });
  }

  // History column: finished blocks on a helix behind the seal.
  const column = new THREE.Group();
  scene.add(column);
  const frozenGeo = new THREE.OctahedronGeometry(0.24, 0);
  frozenGeo.scale(1, 1.5, 1);
  const frozenMat = new THREE.MeshPhysicalMaterial({ color: 0x1a2533, emissive: PALETTE.root, emissiveIntensity: 0.45,
    roughness: 0.1, metalness: 0.3, iridescence: 1, clearcoat: 1, flatShading: true });
  let spine = null;
  function rebuildColumn(blocks) {
    while (column.children.length) column.remove(column.children[0]);
    const shown = Math.min(blocks, 36);
    const pts = [];
    for (let i = 0; i < shown; i++) {
      const a = i * 0.62;
      const p = new THREE.Vector3(ROOT_POS.x + Math.cos(a) * 1.3, ROOT_POS.y - 1.2 - i * 0.26, -2.4 + Math.sin(a) * 1.3);
      const m = new THREE.Mesh(frozenGeo, frozenMat);
      m.position.copy(p);
      m.rotation.set(i * 0.7, i * 1.1, 0);
      column.add(m);
      pts.push(p);
    }
    if (spine) scene.remove(spine);
    const arr = [];
    for (let i = 1; i < pts.length; i++) arr.push(...pts[i - 1].toArray(), ...pts[i].toArray());
    if (pts.length) arr.push(...pts[0].toArray(), ...ROOT_POS.toArray());
    const br = tree.localToWorld(pos[LEVELS][0].clone());
    arr.push(...br.toArray(), ...ROOT_POS.toArray());
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(arr, 3));
    spine = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: PALETTE.root, transparent: true, opacity: 0.4,
      blending: THREE.AdditiveBlending }));
    scene.add(spine);
  }

  // Comets and observation arcs.
  const comets = [];
  const TRAIL = 22;
  function launchComet(index, color) {
    const path = [];
    let i = index;
    for (let l = 0; l < LEVELS; l++) {
      path.push(edgeAt(l, i));
      i >>= 1;
    }
    const head = glowSprite(color, 0.7);
    scene.add(head);
    const tPos = new Float32Array(TRAIL * 3);
    const tAge = new Float32Array(TRAIL);
    for (let k = 0; k < TRAIL; k++) tAge[k] = k / TRAIL;
    const tg = new THREE.BufferGeometry();
    tg.setAttribute("position", new THREE.BufferAttribute(tPos, 3));
    tg.setAttribute("aAge", new THREE.BufferAttribute(tAge, 1));
    const trail = new THREE.Points(tg, new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(color) }, uPixel: { value: pixel } },
      vertexShader: TRAIL_VS, fragmentShader: TRAIL_FS, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));
    scene.add(trail);
    const start = tree.localToWorld(pos[0][index].clone());
    for (let k = 0; k < TRAIL; k++) tPos.set(start.toArray(), k * 3);
    comets.push({ head, trail, tPos, path, t: 0, index, color: new THREE.Color(color) });
  }
  const arcs = [];
  function launchArc(from, leafIdx, color) {
    const to = tree.localToWorld(pos[0][leafIdx].clone());
    const start = from.clone().add(new THREE.Vector3(0, FLOOR_Y, 0));
    const mid = start.clone().lerp(to, 0.5);
    mid.y += 4 + Math.random() * 2;
    const curve = new THREE.QuadraticBezierCurve3(start, mid, to);
    const mat = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uHeat: { value: 0.95 }, uColor: { value: new THREE.Color(color) } },
      vertexShader: EDGE_VS, fragmentShader: ARC_FS,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    const mesh = new THREE.Mesh(new THREE.TubeGeometry(curve, 48, 0.025, 6, false), mat);
    scene.add(mesh);
    arcs.push({ mesh, mat, t: 0 });
  }

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.85, 0.55, 0.18);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());
  const finalPass = new ShaderPass(FINAL_PASS);
  composer.addPass(finalPass);

  // -- state ----------------------------------------------------------------------------
  let mode = "SIM";
  const queue = [];
  let filled = 0;
  let blocks = 0;
  let simCounter = 0;
  let sealFlash = 0;
  let sinceStep = 0;
  let pendingSeal = null;
  let lastSize = 0;

  function resetBlock() {
    for (const lf of leaves) {
      lf.filled = false;
      lf.grow = 0;
      lf.mesh.scale.setScalar(0.001);
      lf.mesh.material.emissiveIntensity = 0;
      lf.halo.material.opacity = 0;
    }
    for (const lvl of inner) for (const n of lvl) n.glow = 0;
    filled = 0;
  }

  function signed(sth) {
    sealFlash = 1;
    shockT = 0;
    scramble = 1.3;
    if (sth) {
      rootHash = sth.rootHash;
      sthBand.draw(`SIGNED TREE HEAD · n=${Number(sth.treeSize).toLocaleString("en")} · ${String(sth.timestamp).replace("T", " ")} · ${String(sth.log || "").slice(0, 24)}…`);
    } else {
      rootHash = simHash(simCounter);
      sthBand.draw(`SIGNED TREE HEAD · model n=${simCounter} · histor.sth/v1`);
    }
  }

  function paintLeaf(lf, color, intensity) {
    lf.mesh.material.emissive.setHex(color);
    lf.mesh.material.emissiveIntensity = intensity;
    lf.halo.material.color.setHex(color);
  }

  function weave(entry) {
    if (filled >= BLOCK) {
      blocks += 1;
      rebuildColumn(blocks);
      resetBlock();
      onPhase("FREEZE", { blocks });
    }
    const idx = filled++;
    const color = leafColor(entry);
    const lf = leaves[idx];
    lf.filled = true;
    lf.grow = 0.0001;
    paintLeaf(lf, color, 0);
    const p = galaxyPoint(true);
    launchArc(p, idx, color);
    setTimeout(() => launchComet(idx, color), reducedMotion ? 0 : 900);
    if (entry.verdict === "fail") ripple(p, PALETTE.fail);
    onPhase(entry.verdict === "fail" ? "CHANGED" : "APPEND", { entry, index: idx });
  }

  function seedStatic(entries, total) {
    resetBlock();
    blocks = total > 0 ? Math.floor((total - 1) / BLOCK) : 0;
    rebuildColumn(blocks);
    for (const e of entries) {
      const idx = filled++;
      if (idx >= BLOCK) break;
      const lf = leaves[idx];
      lf.filled = true;
      lf.grow = 1;
      lf.mesh.scale.setScalar(1);
      paintLeaf(lf, leafColor(e), 1.3);
      lf.halo.material.opacity = 0.35;
    }
    for (let l = 1; l <= LEVELS; l++) inner[l - 1].forEach((n, i) => { n.glow = filled > i * (1 << l) ? 0.55 : 0; });
  }

  function setData({ mode: m, sth, entries, stats }) {
    mode = m;
    if (stats?.targets && stats?.lastRun?.registryEndpoints) {
      setObservedRatio(stats.targets.pinned / stats.lastRun.registryEndpoints);
    }
    if (m !== "LIVE") return;
    const size = Number(sth.treeSize) || 0;
    if (lastSize === 0) {
      seedStatic(entries, size);
      lastSize = size;
      signed(sth);
      onPhase("SIGNED", { sth });
      return;
    }
    if (size > lastSize) {
      queue.push(...entries.filter((e) => e.leaf_index >= lastSize));
      pendingSeal = sth;
      lastSize = size;
    }
  }

  // -- loop -------------------------------------------------------------------------------
  const clock = new THREE.Clock();
  let running = true;
  let visible = true;
  let elapsed = 0;

  function resize() {
    const w = canvas.clientWidth || canvas.parentElement.clientWidth || 800;
    const h = canvas.clientHeight || canvas.parentElement.clientHeight || 500;
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    bloom.setSize(w, h);
    finalPass.uniforms.uRes.value.set(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    fit();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvas.parentElement || canvas);
  resize();

  function frame(dt) {
    const speed = reducedMotion ? 0.35 : 1;
    elapsed += dt;
    galaxyMat.uniforms.uTime.value = elapsed;
    nebulaMat.uniforms.uTime.value = elapsed;
    finalPass.uniforms.uTime.value = elapsed;
    galaxy.rotation.y += dt * 0.02 * speed;

    sinceStep += dt * speed;
    const cadence = mode === "LIVE" ? 0.6 : 1.35;
    if (sinceStep > cadence) {
      sinceStep = 0;
      if (queue.length) {
        weave(queue.shift());
        if (!queue.length && pendingSeal) {
          const sth = pendingSeal;
          pendingSeal = null;
          setTimeout(() => { signed(sth); onPhase("SIGNED", { sth }); }, 1800);
        }
      } else if (mode !== "LIVE") {
        weave(simEntry(simCounter++));
        if (simCounter % 8 === 0) setTimeout(() => { signed(null); onPhase("SIGNED", { sim: true }); }, 1800);
      }
    }

    for (const lf of leaves) {
      const mat = lf.mesh.material;
      if (lf.filled && lf.grow < 1) {
        lf.grow = Math.min(1, lf.grow + dt * 1.8 * speed);
        const s = 1 - Math.pow(1 - lf.grow, 3);
        lf.mesh.scale.setScalar(Math.max(0.001, s * (1 + 0.35 * Math.sin(lf.grow * Math.PI))));
        mat.emissiveIntensity = 1.3 + 3.2 * (1 - lf.grow);
        lf.halo.material.opacity = 0.35 + 0.65 * (1 - lf.grow);
        lf.halo.scale.setScalar(0.9 + 1.4 * (1 - lf.grow));
      } else if (lf.filled) {
        mat.emissiveIntensity += (1.3 - mat.emissiveIntensity) * 0.05;
      }
      lf.mesh.rotation.y += dt * 0.35;
    }
    for (const lvl of inner) {
      for (const n of lvl) {
        n.glow = Math.max(n.glow * 0.985, Math.min(n.glow, 0.55));
        n.mesh.material.emissiveIntensity = 0.1 + n.glow * 2.2;
        n.mesh.rotation.y += dt * 0.6;
        n.mesh.rotation.x += dt * 0.25;
      }
    }
    for (const e of edges) {
      e.mat.uniforms.uTime.value = elapsed;
      e.mat.uniforms.uHeat.value *= Math.pow(0.25, dt);
    }

    for (let i = arcs.length - 1; i >= 0; i--) {
      const a = arcs[i];
      a.t += dt * 0.9 * speed;
      a.mat.uniforms.uTime.value = Math.min(1.25, a.t);
      a.mat.uniforms.uHeat.value = a.t < 1 ? 0.95 : Math.max(0, 0.95 - (a.t - 1) * 2.5);
      if (a.t > 1.5) {
        scene.remove(a.mesh);
        a.mesh.geometry.dispose();
        a.mat.dispose();
        arcs.splice(i, 1);
      }
    }
    for (let i = comets.length - 1; i >= 0; i--) {
      const c = comets[i];
      c.t += dt * 1.9 * speed;
      const seg = Math.min(c.path.length - 1, Math.floor(c.t));
      const f = Math.min(1, c.t - seg);
      const edge = c.path[seg];
      const p = tree.localToWorld(edge.curve.getPoint(f));
      c.head.position.copy(p);
      edge.mat.uniforms.uHeat.value = Math.max(edge.mat.uniforms.uHeat.value, 1);
      edge.mat.uniforms.uHot.value.copy(c.color);
      if (f > 0.9) {
        const node = inner[seg][c.index >> (seg + 1)];
        if (node) node.glow = 1.4;
      }
      c.tPos.copyWithin(3, 0, (TRAIL - 1) * 3);
      c.tPos.set(p.toArray(), 0);
      c.trail.geometry.attributes.position.needsUpdate = true;
      if (c.t >= c.path.length) {
        scene.remove(c.head);
        scene.remove(c.trail);
        c.trail.geometry.dispose();
        comets.splice(i, 1);
      }
    }
    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i];
      r.t += dt * 0.7;
      r.m.scale.setScalar(0.2 + r.t * 3.5);
      r.m.material.opacity = Math.max(0, 0.9 - r.t);
      if (r.t > 1) {
        scene.remove(r.m);
        r.m.geometry.dispose();
        r.m.material.dispose();
        ripples.splice(i, 1);
      }
    }

    // Seal.
    if (scramble > 0) {
      scramble -= dt;
      const settled = Math.floor(64 * Math.max(0, 1 - scramble / 1.3));
      let s = rootHash.slice(0, settled);
      for (let k = settled; k < 64; k++) s += "0123456789abcdef"[Math.floor(Math.random() * 16)];
      hashBand.draw(scramble > 0 ? s : rootHash);
    }
    sealFlash = Math.max(0, sealFlash - dt * 0.6);
    hashBand.mesh.rotation.y += dt * (0.12 + sealFlash * 1.6);
    sthBand.mesh.rotation.y -= dt * (0.08 + sealFlash * 1.1);
    tickRing.rotation.z += dt * 0.2;
    rootMat.emissiveIntensity = 0.45 + sealFlash * 2.6 + Math.sin(elapsed * 1.4) * 0.1;
    coreGlow.material.opacity = 0.35 + sealFlash * 0.5;
    coreGlow.scale.setScalar(1.7 * (1 + sealFlash * 1.2 + Math.sin(elapsed * 2.1) * 0.05));
    logRoot.rotation.y += dt * 0.3;
    logRoot.rotation.x += dt * 0.11;
    seal.position.y = ROOT_POS.y + Math.sin(elapsed * 0.7) * 0.08;
    if (shockT < 1) {
      shockT += dt * 0.45;
      shock.scale.setScalar(1 + shockT * 22);
      shockMat.opacity = Math.max(0, 0.85 * (1 - shockT));
    } else {
      shockMat.opacity = 0;
    }
    column.children.forEach((m, i) => { m.rotation.y += dt * (0.25 + (i % 3) * 0.06); });

    controls.update();
    composer.render();
  }

  function loop() {
    if (!running) return;
    requestAnimationFrame(loop);
    if (!visible || document.hidden) {
      clock.getDelta();
      return;
    }
    frame(Math.min(0.05, clock.getDelta()));
  }

  const io = new IntersectionObserver((items) => { visible = items.some((it) => it.isIntersecting); });
  io.observe(canvas);
  seedStatic(Array.from({ length: 11 }, (_, i) => simEntry(i)), 11);
  simCounter = 11;
  frame(0.016); // one synchronous frame, so the scene exists even where rAF is throttled
  loop();

  const handle = {
    setData,
    /** Advance the scene by hand — for environments where requestAnimationFrame never fires. */
    step(frames = 60, dt = 1 / 60) {
      for (let i = 0; i < frames; i++) frame(dt);
    },
    dispose() {
      running = false;
      ro.disconnect();
      io.disconnect();
      controls.dispose();
      renderer.dispose();
    },
  };
  window.__histor_loom = handle;
  return handle;
}
