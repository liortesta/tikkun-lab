/* A mast cell, in three dimensions, showing what the engine computed.
 *
 * Not decoration. Every element on screen is bound to a number the simulation
 * produced, and nothing is drawn that the model does not know:
 *
 *   receptors   FcepsilonRI on the membrane. Pale when bare, blue once they
 *               carry milk-specific IgE, bright when an allergen has bridged
 *               two of them. The proportions come from sensitizedReceptors()
 *               and crosslinks().
 *   allergen    beta-lactoglobulin drifting in, scaled to the free allergen
 *               concentration at the mucosa.
 *   granules    histamine leaving the cell, emitted at the rate degranulation
 *               is actually running at.
 *
 * Raw WebGL2, no library: the page has to stay small and start instantly, and
 * a general-purpose 3D engine is several hundred kilobytes to draw one sphere.
 */

const VERTEX_SPHERE = `#version 300 es
precision highp float;
in vec3 position;
in vec3 normal;
uniform mat4 uProjection;
uniform mat4 uView;
uniform mat4 uModel;
out vec3 vNormal;
out vec3 vViewDir;
void main() {
  vec4 world = uModel * vec4(position, 1.0);
  vNormal = mat3(uModel) * normal;
  vViewDir = -(uView * world).xyz;
  gl_Position = uProjection * uView * world;
}`;

const FRAGMENT_SPHERE = `#version 300 es
precision highp float;
in vec3 vNormal;
in vec3 vViewDir;
uniform vec3 uBase;
uniform vec3 uRim;
out vec4 fragColor;
void main() {
  vec3 n = normalize(vNormal);
  vec3 v = normalize(vViewDir);
  vec3 light = normalize(vec3(0.45, 0.75, 0.8));
  float lambert = max(dot(n, light), 0.0);
  float wrap = lambert * 0.72 + 0.28;
  // A rim term reads as a membrane edge, which is what makes a flat-shaded
  // sphere look like a cell rather than a ball.
  float rim = pow(1.0 - max(dot(n, v), 0.0), 2.6);
  vec3 colour = uBase * wrap + uRim * rim * 0.85;
  fragColor = vec4(colour, 1.0);
}`;

const VERTEX_POINTS = `#version 300 es
precision highp float;
in vec3 position;
in float state;      // 0 bare, 1 sensitised, 2 crosslinked
in float size;
uniform mat4 uProjection;
uniform mat4 uView;
uniform mat4 uModel;
uniform float uScale;
out float vState;
out float vFacing;
void main() {
  vec4 world = uModel * vec4(position, 1.0);
  vec4 eye = uView * world;
  vState = state;
  // Points on the far side of the membrane dim rather than vanish, so the cell
  // reads as a volume instead of a disc.
  vec3 n = normalize(mat3(uModel) * position);
  vFacing = dot(n, normalize(-(uView * vec4(0.0, 0.0, 0.0, 1.0)).xyz - eye.xyz));
  gl_Position = uProjection * eye;
  gl_PointSize = size * uScale / max(-eye.z, 0.1);
}`;

const FRAGMENT_POINTS = `#version 300 es
precision highp float;
in float vState;
in float vFacing;
uniform vec3 uBare;
uniform vec3 uSensitised;
uniform vec3 uCrosslinked;
out vec4 fragColor;
void main() {
  float r = length(gl_PointCoord - vec2(0.5));
  if (r > 0.5) discard;
  vec3 colour = vState < 0.5 ? uBare : (vState < 1.5 ? uSensitised : uCrosslinked);
  float depth = clamp(vFacing * 0.5 + 0.72, 0.30, 1.0);
  // Bare receptors stay a soft-edged texture; the ones that matter get a hard
  // core and a halo, so a few percent of the membrane still reads at a glance.
  float alpha = vState < 0.5
    ? smoothstep(0.5, 0.12, r) * 0.8
    : (vState < 1.5
        ? smoothstep(0.5, 0.28, r)
        : smoothstep(0.5, 0.0, r) * 0.45 + smoothstep(0.28, 0.14, r));
  float glow = vState > 1.5 ? 1.5 : 1.1;
  fragColor = vec4(colour * glow, clamp(alpha, 0.0, 1.0) * depth);
}`;

const VERTEX_PARTICLE = `#version 300 es
precision highp float;
in vec3 position;
in float size;
in float fade;
uniform mat4 uProjection;
uniform mat4 uView;
uniform float uScale;
out float vFade;
void main() {
  vec4 eye = uView * vec4(position, 1.0);
  vFade = fade;
  gl_Position = uProjection * eye;
  gl_PointSize = size * uScale / max(-eye.z, 0.1);
}`;

const FRAGMENT_PARTICLE = `#version 300 es
precision highp float;
in float vFade;
uniform vec3 uColour;
out vec4 fragColor;
void main() {
  float r = length(gl_PointCoord - vec2(0.5));
  if (r > 0.5) discard;
  float core = smoothstep(0.5, 0.0, r);
  fragColor = vec4(uColour, core * core * vFade);
}`;

/* ---- small matrix helpers, column-major to match WebGL ---- */

function perspective(fovY, aspect, near, far) {
  const f = 1 / Math.tan(fovY / 2);
  const d = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * d, -1, 0, 0, 2 * far * near * d, 0];
}

function lookAtOrigin(distance) {
  return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -distance, 1];
}

function rotationYX(yaw, pitch) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cx = Math.cos(pitch), sx = Math.sin(pitch);
  // R = Rx * Ry
  return [
    cy, sy * sx, -sy * cx, 0,
    0, cx, sx, 0,
    sy, -cy * sx, cy * cx, 0,
    0, 0, 0, 1,
  ];
}

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error('shader: ' + gl.getShaderInfoLog(shader));
  }
  return shader;
}

function program(gl, vertexSource, fragmentSource) {
  const p = gl.createProgram();
  gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vertexSource));
  gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fragmentSource));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    throw new Error('link: ' + gl.getProgramInfoLog(p));
  }
  return p;
}

function uvSphere(segments, rings) {
  const positions = [];
  const normals = [];
  const indices = [];
  for (let y = 0; y <= rings; y++) {
    const phi = (y / rings) * Math.PI;
    for (let x = 0; x <= segments; x++) {
      const theta = (x / segments) * Math.PI * 2;
      const nx = Math.sin(phi) * Math.cos(theta);
      const ny = Math.cos(phi);
      const nz = Math.sin(phi) * Math.sin(theta);
      positions.push(nx, ny, nz);
      normals.push(nx, ny, nz);
    }
  }
  for (let y = 0; y < rings; y++) {
    for (let x = 0; x < segments; x++) {
      const a = y * (segments + 1) + x;
      const b = a + segments + 1;
      indices.push(a, b, a + 1, b, b + 1, a + 1);
    }
  }
  return {
    positions: new Float32Array(positions),
    normals: new Float32Array(normals),
    indices: new Uint16Array(indices),
  };
}

/* Fibonacci sphere: receptors spread evenly rather than bunching at the poles,
   which a naive lat/long placement does and which reads as an artefact. */
function fibonacciPoints(count) {
  const out = new Float32Array(count * 3);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2;
    const radius = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * i;
    out[i * 3] = Math.cos(theta) * radius;
    out[i * 3 + 1] = y;
    out[i * 3 + 2] = Math.sin(theta) * radius;
  }
  return out;
}

const RECEPTOR_COUNT = 620;
const ALLERGEN_MAX = 260;
const GRANULE_MAX = 340;

export function createCellView(canvas, options = {}) {
  const gl = canvas.getContext('webgl2', { antialias: true, alpha: true, premultipliedAlpha: false });
  if (!gl) return null;

  let programs;
  try {
    programs = {
      sphere: program(gl, VERTEX_SPHERE, FRAGMENT_SPHERE),
      points: program(gl, VERTEX_POINTS, FRAGMENT_POINTS),
      particle: program(gl, VERTEX_PARTICLE, FRAGMENT_PARTICLE),
    };
  } catch (error) {
    console.error('cell view unavailable:', error);
    return null;
  }

  const sphere = uvSphere(56, 36);
  const buffers = {
    spherePos: gl.createBuffer(),
    sphereNorm: gl.createBuffer(),
    sphereIdx: gl.createBuffer(),
    receptorPos: gl.createBuffer(),
    receptorState: gl.createBuffer(),
    receptorSize: gl.createBuffer(),
    particlePos: gl.createBuffer(),
    particleSize: gl.createBuffer(),
    particleFade: gl.createBuffer(),
  };

  gl.bindBuffer(gl.ARRAY_BUFFER, buffers.spherePos);
  gl.bufferData(gl.ARRAY_BUFFER, sphere.positions, gl.STATIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffers.sphereNorm);
  gl.bufferData(gl.ARRAY_BUFFER, sphere.normals, gl.STATIC_DRAW);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, buffers.sphereIdx);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, sphere.indices, gl.STATIC_DRAW);

  const receptorPositions = fibonacciPoints(RECEPTOR_COUNT);
  const receptorState = new Float32Array(RECEPTOR_COUNT);
  const receptorSize = new Float32Array(RECEPTOR_COUNT).fill(3.4);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffers.receptorPos);
  gl.bufferData(gl.ARRAY_BUFFER, receptorPositions, gl.STATIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffers.receptorSize);
  gl.bufferData(gl.ARRAY_BUFFER, receptorSize, gl.STATIC_DRAW);

  /* particles: allergen drifting in, granules leaving */
  const allergen = [];
  const granules = [];
  const particlePos = new Float32Array((ALLERGEN_MAX + GRANULE_MAX) * 3);
  const particleSize = new Float32Array(ALLERGEN_MAX + GRANULE_MAX);
  const particleFade = new Float32Array(ALLERGEN_MAX + GRANULE_MAX);

  let state = { sensitised: 0, crosslinked: 0, activation: 0, allergen: 0, reaction: false };
  let palette = options.palette || {};
  let yaw = 0.6;
  let pitch = -0.25;
  let autoSpin = true;
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  let raf = 0;
  let lastTime = 0;
  let reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function seedAllergen() {
    const target = Math.round(ALLERGEN_MAX * state.allergen);
    while (allergen.length < target) {
      const theta = Math.random() * Math.PI * 2;
      const z = Math.random() * 2 - 1;
      const r = Math.sqrt(1 - z * z);
      const distance = 2.4 + Math.random() * 1.6;
      allergen.push({
        x: Math.cos(theta) * r * distance,
        y: z * distance,
        z: Math.sin(theta) * r * distance,
        speed: 0.12 + Math.random() * 0.22,
        size: 12 + Math.random() * 7,
      });
    }
    while (allergen.length > target) allergen.pop();
  }

  function step(dt) {
    seedAllergen();
    for (const p of allergen) {
      const d = Math.hypot(p.x, p.y, p.z);
      if (d < 1.06) {
        // Reached the membrane: send it back out so the field keeps moving
        // rather than collapsing onto the surface.
        const scale = (3.4 + Math.random()) / d;
        p.x *= scale; p.y *= scale; p.z *= scale;
        continue;
      }
      const pull = (p.speed * dt) / d;
      p.x -= p.x * pull; p.y -= p.y * pull; p.z -= p.z * pull;
    }

    // Granules leave at the rate degranulation is actually running.
    const emit = state.activation * dt * 26;
    for (let i = 0; i < emit && granules.length < GRANULE_MAX; i++) {
      const theta = Math.random() * Math.PI * 2;
      const z = Math.random() * 2 - 1;
      const r = Math.sqrt(1 - z * z);
      const nx = Math.cos(theta) * r, ny = z, nz = Math.sin(theta) * r;
      granules.push({ x: nx, y: ny, z: nz, vx: nx, vy: ny, vz: nz,
                      life: 1, size: 9 + Math.random() * 9 });
    }
    for (let i = granules.length - 1; i >= 0; i--) {
      const g = granules[i];
      const speed = 0.9 * dt;
      g.x += g.vx * speed; g.y += g.vy * speed; g.z += g.vz * speed;
      g.life -= dt * 0.55;
      if (g.life <= 0) granules.splice(i, 1);
    }
  }

  function writeParticles() {
    let n = 0;
    for (const p of allergen) {
      particlePos[n * 3] = p.x; particlePos[n * 3 + 1] = p.y; particlePos[n * 3 + 2] = p.z;
      particleSize[n] = p.size; particleFade[n] = 0.75; n++;
    }
    const allergenCount = n;
    for (const g of granules) {
      particlePos[n * 3] = g.x; particlePos[n * 3 + 1] = g.y; particlePos[n * 3 + 2] = g.z;
      particleSize[n] = g.size; particleFade[n] = Math.max(0, g.life); n++;
    }
    return { allergenCount, total: n };
  }

  function updateReceptorStates() {
    // Only a few percent of receptors carry milk-specific IgE in a real patient,
    // so the three states have to differ in size as well as colour. At a uniform
    // size the handful that matter disappear into the crowd, and the membrane
    // reads as texture instead of as a population.
    const sensitised = Math.max(
      state.sensitised > 0 ? 1 : 0, Math.round(RECEPTOR_COUNT * state.sensitised));
    // Bridged receptors are genuinely rare — around 200 out of a quarter million,
    // which is under a tenth of a percent. Rounding that down to nothing would
    // hide the model's most striking fact: it takes almost none of them to fire
    // the cell. So a bridged receptor that exists is always drawn, and drawn
    // large enough that a single one is unmistakable.
    const crosslinked = state.crosslinked > 0
      ? Math.max(1, Math.round(sensitised * state.crosslinked)) : 0;
    for (let i = 0; i < RECEPTOR_COUNT; i++) {
      receptorState[i] = i < crosslinked ? 2 : (i < sensitised ? 1 : 0);
      receptorSize[i] = i < crosslinked ? 21 : (i < sensitised ? 9 : 3.4);
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.receptorState);
    gl.bufferData(gl.ARRAY_BUFFER, receptorState, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffers.receptorSize);
    gl.bufferData(gl.ARRAY_BUFFER, receptorSize, gl.DYNAMIC_DRAW);
  }

  function bindAttribute(prog, name, buffer, size) {
    const location = gl.getAttribLocation(prog, name);
    if (location < 0) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
  }

  function colour(name, fallback) {
    const value = palette[name] || fallback;
    if (Array.isArray(value)) return value;
    const hex = String(value).trim().replace('#', '');
    const full = hex.length === 3 ? hex.split('').map(c => c + c).join('') : hex;
    const n = parseInt(full, 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }

  function render(now) {
    raf = requestAnimationFrame(render);
    const dt = lastTime ? Math.min(0.05, (now - lastTime) / 1000) : 0.016;
    lastTime = now;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    gl.viewport(0, 0, width, height);

    if (autoSpin && !reduceMotion) yaw += dt * 0.18;
    if (!reduceMotion) step(dt);

    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    const projection = perspective(0.85, width / height, 0.1, 40);
    const view = lookAtOrigin(3.55);
    const model = rotationYX(yaw, pitch);

    /* membrane */
    gl.useProgram(programs.sphere);
    gl.uniformMatrix4fv(gl.getUniformLocation(programs.sphere, 'uProjection'), false, projection);
    gl.uniformMatrix4fv(gl.getUniformLocation(programs.sphere, 'uView'), false, view);
    gl.uniformMatrix4fv(gl.getUniformLocation(programs.sphere, 'uModel'), false, model);
    gl.uniform3fv(gl.getUniformLocation(programs.sphere, 'uBase'), colour('membrane', '#16233a'));
    gl.uniform3fv(gl.getUniformLocation(programs.sphere, 'uRim'), colour('rim', '#2f6fd0'));
    bindAttribute(programs.sphere, 'position', buffers.spherePos, 3);
    bindAttribute(programs.sphere, 'normal', buffers.sphereNorm, 3);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, buffers.sphereIdx);
    gl.drawElements(gl.TRIANGLES, sphere.indices.length, gl.UNSIGNED_SHORT, 0);

    /* receptors */
    gl.useProgram(programs.points);
    gl.uniformMatrix4fv(gl.getUniformLocation(programs.points, 'uProjection'), false, projection);
    gl.uniformMatrix4fv(gl.getUniformLocation(programs.points, 'uView'), false, view);
    gl.uniformMatrix4fv(gl.getUniformLocation(programs.points, 'uModel'), false, model);
    gl.uniform1f(gl.getUniformLocation(programs.points, 'uScale'), height / dpr / 90);
    gl.uniform3fv(gl.getUniformLocation(programs.points, 'uBare'), colour('bare', '#43546e'));
    gl.uniform3fv(gl.getUniformLocation(programs.points, 'uSensitised'), colour('sensitised', '#4d9bff'));
    gl.uniform3fv(gl.getUniformLocation(programs.points, 'uCrosslinked'), colour('crosslinked', '#ffd166'));
    bindAttribute(programs.points, 'position', buffers.receptorPos, 3);
    bindAttribute(programs.points, 'state', buffers.receptorState, 1);
    bindAttribute(programs.points, 'size', buffers.receptorSize, 1);
    gl.drawArrays(gl.POINTS, 0, RECEPTOR_COUNT);

    /* allergen and granules, additive so they read as free-floating */
    const { allergenCount, total } = writeParticles();
    if (total) {
      gl.depthMask(false);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      gl.useProgram(programs.particle);
      gl.uniformMatrix4fv(gl.getUniformLocation(programs.particle, 'uProjection'), false, projection);
      gl.uniformMatrix4fv(gl.getUniformLocation(programs.particle, 'uView'), false, view);
      gl.uniform1f(gl.getUniformLocation(programs.particle, 'uScale'), height / dpr / 90);

      gl.bindBuffer(gl.ARRAY_BUFFER, buffers.particlePos);
      gl.bufferData(gl.ARRAY_BUFFER, particlePos, gl.DYNAMIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffers.particleSize);
      gl.bufferData(gl.ARRAY_BUFFER, particleSize, gl.DYNAMIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffers.particleFade);
      gl.bufferData(gl.ARRAY_BUFFER, particleFade, gl.DYNAMIC_DRAW);
      bindAttribute(programs.particle, 'position', buffers.particlePos, 3);
      bindAttribute(programs.particle, 'size', buffers.particleSize, 1);
      bindAttribute(programs.particle, 'fade', buffers.particleFade, 1);

      // Allergen is drawn in the rotating frame's colour, granules in another,
      // so the two flows never read as the same thing moving both ways.
      gl.uniform3fv(gl.getUniformLocation(programs.particle, 'uColour'), colour('allergenDot', '#7fe3c0'));
      gl.drawArrays(gl.POINTS, 0, allergenCount);
      if (total > allergenCount) {
        gl.uniform3fv(gl.getUniformLocation(programs.particle, 'uColour'), colour('granule', '#ff8f6b'));
        gl.drawArrays(gl.POINTS, allergenCount, total - allergenCount);
      }
      gl.depthMask(true);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    }
  }

  /* ---- interaction ---- */
  const onDown = event => {
    dragging = true; autoSpin = false;
    lastX = event.clientX ?? event.touches[0].clientX;
    lastY = event.clientY ?? event.touches[0].clientY;
    canvas.setPointerCapture?.(event.pointerId);
  };
  const onMove = event => {
    if (!dragging) return;
    const x = event.clientX ?? event.touches[0].clientX;
    const y = event.clientY ?? event.touches[0].clientY;
    yaw -= (x - lastX) * 0.008;
    pitch = Math.max(-1.2, Math.min(1.2, pitch - (y - lastY) * 0.008));
    lastX = x; lastY = y;
    event.preventDefault();
  };
  const onUp = () => { dragging = false; };

  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointercancel', onUp);
  canvas.addEventListener('pointerleave', onUp);
  canvas.style.touchAction = 'none';

  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  const onMotionChange = () => { reduceMotion = motionQuery.matches; };
  motionQuery.addEventListener?.('change', onMotionChange);

  updateReceptorStates();
  raf = requestAnimationFrame(render);

  return {
    /** Bind the view to a fresh engine result. All four values are fractions. */
    update(next) {
      const changedReceptors =
        next.sensitised !== state.sensitised || next.crosslinked !== state.crosslinked;
      state = { ...state, ...next };
      if (changedReceptors) updateReceptorStates();
    },
    setPalette(next) { palette = next; },
    resume() { autoSpin = true; },
    destroy() {
      cancelAnimationFrame(raf);
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('pointercancel', onUp);
      canvas.removeEventListener('pointerleave', onUp);
      motionQuery.removeEventListener?.('change', onMotionChange);
    },
  };
}
