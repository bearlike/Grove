import * as THREE from "../vendor/three.module.js";
import { EffectComposer } from "../vendor/postprocessing/EffectComposer.js";
import { RenderPass } from "../vendor/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "../vendor/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "../vendor/postprocessing/OutputPass.js";
import { bake, shareRobotGeometry } from "./bake.js";
import { buildBelt, buildFloor, moveSlats } from "./belt.js";
import { FrameGovernor, POLICY } from "./frame-governor.js";
import { GpuTimer } from "./gpu-timer.js";
import { AGENT_BADGES, BELT_TOP, COIL_DELAY, PALETTE, ROBOT_COUNT, TRAVEL } from "./palette.js";
import { roundedBox } from "./parts.js";
import { applyFade, buildRobot, riderOpacity } from "./robot.js";
import { buildStation } from "./station.js";

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
// Mirrors the stylesheet's breakpoint: above it the scene is the right column
// of a split hero, below it a band under the copy.
const SPLIT_LAYOUT = window.matchMedia("(min-width: 901px)");
const ARC_SEGMENTS = 12;
const UP = new THREE.Vector3(0, 1, 0);
// The second link ramps in over delayed travel LINK_IN, holds at full
// strength until LINK_OUT, then fades. The check switches on the moment the
// bolt is at full strength: that is when it reads as having landed.
const LINK_IN = [-1.2, -0.35];
const LINK_OUT = [0.35, 1.2];
// The activation station's box in station space: where the action is, and
// Face texture size: a face is about 200 CSS px wide on a large monitor and
// is seen at a slant, so 1024 texels keep the glyph edges clean under MSAA.
const DISPLAY_TEXELS = 1024;

// The landing page's factory line: agent robots ride a conveyor between two
// activation coils, are linked to both, and leave with a verified check on
// their face.
//
// Lifecycle: build the models, wait for the face textures, bake, render one
// frame, fade the canvas in, then animate only while visible, on screen and
// allowed to move. Construction is split into `add*` steps so the geometry
// probe (tests/docs/landing_geometry.mjs) can build and inspect each model
// without a WebGL context.
export class FactoryScene {
  // `logos` is the directory holding the agent display glyphs. The entry
  // module resolves it against its own URL, so the scene works from any route.
  constructor(container, { logos } = {}) {
    this.container = container;
    this.canvas = container.querySelector("[data-grove-factory-canvas]");
    this.logos = logos;
    this.paused = REDUCED_MOTION.matches;
    this.elapsed = 0;
    this.lastTime = 0;
    this.robotRunners = [];
    this.frame = null;
    this.visible = true;
    this.started = false;
    this.pendingQuality = null;
    // Read-only diagnostics for a console probe: the governor's rung, the
    // last measured GPU ms and how many frames and quality changes so far.
    this.stats = { frames: 0, changes: 0, gpuMs: null, quality: null };
    Object.defineProperty(this.canvas ?? {}, "groveStats", { value: this.stats });
    this.textureLoads = [];
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.initialize();
  }

  async initialize() {
    try {
      this.build();
      await Promise.all(this.textureLoads);
      this.updateRobots();
      this.bake();
      this.resizeObserver.observe(this.container);
      this.resize();
      this.canvas.dataset.ready = "true";
      REDUCED_MOTION.addEventListener("change", (event) => {
        this.paused = event.matches;
        this.schedule();
      });
      document.addEventListener("visibilitychange", () => this.schedule());
      this.intersectionObserver = new IntersectionObserver(([entry]) => {
        this.visible = entry.isIntersecting;
        this.schedule();
      });
      this.intersectionObserver.observe(this.container);
      if (!REDUCED_MOTION.matches) {
        // Reveal the already-rendered, fully textured scene before moving it.
        await this.canvas.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 700, easing: "ease-out", fill: "forwards" }).finished;
      }
      this.canvas.style.opacity = "1";
      this.started = true;
      this.schedule();
    } catch (error) {
      // Keep the hero and its links usable without substituting different art.
      this.resizeObserver.disconnect();
      this.canvas.remove();
      console.warn("Grove factory scene unavailable", error);
    }
  }

  build() {
    // Antialias the composer's scene target, not the final canvas copy, and
    // bound both DPR and samples: every bloom buffer scales with them.
    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: false, alpha: false, powerPreference: "high-performance" });
    const { width, height } = this.container.getBoundingClientRect();
    this.governor = new FrameGovernor({
      ...FrameGovernor.range({
        devicePixelRatio: window.devicePixelRatio || 1,
        cssPixels: width * height,
        maxSamples: this.renderer.capabilities.maxSamples,
      }),
      software: isSoftwareRenderer(this.renderer.getContext()),
    });
    this.gpuTimer = new GpuTimer(this.renderer.getContext());
    this.stats.quality = { ...this.governor.quality };
    this.stats.gpuTimer = this.gpuTimer.available;
    this.renderer.setPixelRatio(this.governor.quality.pixelRatio);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.9;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(PALETTE.night);
    this.scene.fog = new THREE.Fog(PALETTE.night, 22, 46);
    this.scene.environment = this.makeEnvironment();
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.1, 120);

    this.addLights();
    this.addBelt();
    this.addScanner();
    this.addRobots();
    this.addAtmosphere();

    // Bloom is what makes the eyes, the pane and the neon edges read as light
    // sources instead of flat coloured paint.
    const target = new THREE.WebGLRenderTarget(1, 1, {
      type: THREE.HalfFloatType,
      samples: this.governor.quality.samples,
    });
    this.composer = new EffectComposer(this.renderer, target);
    // Only the scene is drawn with geometry edges to antialias. The composer
    // clones its target for the ping-pong buffer, which then multisampled the
    // bloom copies too, for nothing. RenderPass draws into the read buffer
    // and the chain swaps once a frame (output), so the read buffer is the
    // scene target on every frame.
    this.sceneTarget = this.composer.readBuffer;
    this.composer.writeBuffer.samples = 0;
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.26, 0.4, 0.97);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());
  }

  // Neutral studio: soft white overhead, grey on the scanner side, ember on
  // the near side. Chrome only looks like chrome when it has something to
  // reflect. Rendered once into a prefiltered map, then thrown away.
  makeEnvironment() {
    const studio = new THREE.Scene();
    studio.background = new THREE.Color(0x121010);
    for (const [color, width, height, position] of [
      [PALETTE.softWhite, 18, 6, [0, 14, 0]],
      [0xcfc6c0, 6, 20, [-12, 4, -8]],
      [PALETTE.ember, 6, 20, [12, 2, 10]],
      [0x8a7a72, 24, 4, [0, 3, -16]],
    ]) {
      const card = new THREE.Mesh(new THREE.PlaneGeometry(width, height), new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }));
      card.position.fromArray(position);
      card.lookAt(0, 0, 0);
      studio.add(card);
    }
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const target = pmrem.fromScene(studio, 0.05);
    pmrem.dispose();
    studio.traverse((object) => {
      if (object.isMesh) { object.geometry.dispose(); object.material.dispose(); }
    });
    return target.texture;
  }

  addLights() {
    this.scene.add(new THREE.HemisphereLight(0xf2e8e0, 0x140e0c, 0.5));
    const key = new THREE.DirectionalLight(PALETTE.warmWhite, 1.8);
    key.position.set(-6, 14, 6);
    key.castShadow = true;
    key.shadow.mapSize.set(this.governor.quality.shadow, this.governor.quality.shadow);
    this.keyLight = key;
    Object.assign(key.shadow.camera, { left: -14, right: 14, top: 16, bottom: -16 });
    key.shadow.bias = -0.0008;
    this.scene.add(key);
    // Fill from the camera's side of the belt. Every other source sits above
    // or behind the robots, so the faces the viewer actually sees had nothing
    // to reflect and read as black. Directional and wide rather than a point
    // light, so it lifts the whole queue evenly and adds no hotspot.
    const fill = new THREE.DirectionalLight(PALETTE.softWhite, 0.7);
    fill.position.set(12, 6, 18);
    this.scene.add(fill);
    for (const [color, intensity, position, distance] of [
      [PALETTE.warmWhite, 7, [-4, 5, -7], 22],
      [PALETTE.ember, 7, [4, 2.2, 8], 20],
      [PALETTE.warmWhite, 3, [7, 0.5, -3], 18],
    ]) {
      const light = new THREE.PointLight(color, intensity, distance, 1.6);
      light.position.fromArray(position);
      this.scene.add(light);
    }
  }

  // Kept on the class for the geometry probe, which checks the bevel budget.
  roundedBox(width, height, depth, radius, surface, segments) {
    return roundedBox(width, height, depth, radius, surface, segments);
  }

  addBelt() {
    this.belt = buildBelt();
    this.beltSlats = this.belt.getObjectByName("belt-slats");
    this.scene.add(this.belt);
  }

  updateBelt(distance = this.conveyorOffset() * TRAVEL * 2) {
    moveSlats(this.beltSlats, distance);
  }

  addScanner() {
    Object.assign(this, buildStation());
    this.belt.add(this.station);
  }

  addAtmosphere() {
    this.scene.add(...buildFloor(this.belt.rotation.y));
  }

  makeRobot(spec) {
    return buildRobot(spec);
  }

  makeCheckTexture() {
    // Drawn in a 256 unit design space and rasterised at DISPLAY_TEXELS.
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = DISPLAY_TEXELS;
    const context = canvas.getContext("2d");
    context.scale(DISPLAY_TEXELS / 256, DISPLAY_TEXELS / 256);
    context.strokeStyle = "#ffffff";
    context.lineWidth = 16;
    context.lineCap = "round";
    context.lineJoin = "round";
    context.beginPath();
    context.roundRect(24, 24, 208, 208, 32);
    context.stroke();
    context.beginPath();
    context.moveTo(68, 128);
    context.lineTo(110, 170);
    context.lineTo(188, 88);
    context.stroke();
    return this.displayTexture(new THREE.CanvasTexture(canvas));
  }

  // Mipmapped and anisotropically filtered: the faces are seen at a slant
  // and shrink with distance, where a plain bilinear lookup shimmers.
  displayTexture(texture) {
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = this.renderer?.capabilities.getMaxAnisotropy() ?? 1;
    return texture;
  }

  // The glyphs are SVGs whose intrinsic size is 256 px, which is what an
  // image load rasterises them at. Rasterise at DISPLAY_TEXELS instead.
  async loadGlyph(url) {
    const image = new Image();
    image.decoding = "async";
    image.src = url;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = DISPLAY_TEXELS;
    canvas.getContext("2d").drawImage(image, 0, 0, DISPLAY_TEXELS, DISPLAY_TEXELS);
    return this.displayTexture(new THREE.CanvasTexture(canvas));
  }

  applyRobotBadge(runner) {
    const glyph = new URL(runner.badge, this.logos).href;
    this.textureLoads.push(this.loadGlyph(glyph).then((texture) => {
      runner.logoTexture = texture;
    }));
  }

  addRobots() {
    this.checkTexture = this.makeCheckTexture();
    this.robotRunners = Array.from({ length: ROBOT_COUNT }, (_, index) => ({
      phase: index / ROBOT_COUNT,
      badge: AGENT_BADGES[index % AGENT_BADGES.length],
    }));
    for (const runner of this.robotRunners) {
      runner.group = this.makeRobot(runner);
      // Seat the lowest tread on the deck even when the model's proportions change.
      const bounds = new THREE.Box3().setFromObject(runner.group);
      runner.height = BELT_TOP - bounds.min.y;
      runner.rear = -bounds.min.z;
      runner.front = bounds.max.z;
      this.applyRobotBadge(runner);
      this.belt.add(runner.group);
    }
    this.addActivationArcs();
  }

  // Collapse the authored graphs into a handful of draws, once, before the
  // first frame. Everything that animates stays live, named here by role: each
  // robot's display (it swaps textures), the coil emitters and indicator bands
  // (each glows to its own level), the activation arcs and message packets
  // (they come and go), the slats and the riders. Visibility at bake time is
  // not a signal: it is whatever the first frame happened to show.
  bake() {
    const live = new Set(["display", "coil-emitter", "message-capsule"]);
    const bands = new Set(this.coilIndicators.map(({ material }) => material));
    const riders = new Set(this.robotRunners.map((runner) => runner.group));
    const keep = (part) => live.has(part.name) || bands.has(part.material)
      || part === this.arcGroup || part === this.beltSlats || part.name.startsWith("baked:");
    for (const rider of riders) bake(rider, keep);
    shareRobotGeometry([...riders]);
    bake(this.station, keep);
    bake(this.belt, (part) => keep(part) || riders.has(part) || part === this.station);
    this.scene.updateMatrixWorld(true);
  }

  addActivationArcs() {
    this.arcGroup = new THREE.Group();
    this.arcGroup.name = "activation-arcs";
    this.station.add(this.arcGroup);
    this.arcSegments = ARC_SEGMENTS;
    this.arcPose = new THREE.Object3D();
    this.arcDirection = new THREE.Vector3();
    // Fixed instanced cylinder segments: a fine core and a faint halo, with
    // no geometry rebuilt per frame.
    const segment = new THREE.CylinderGeometry(1, 1, 1, 6);
    for (const runner of this.robotRunners) {
      runner.arcs = runner.antennaTips.map((antenna, side) => {
        const arc = new THREE.Group();
        arc.visible = false;
        arc.userData = { antenna, emitter: this.coilEmitters[side], points: Array.from({ length: ARC_SEGMENTS + 1 }, () => new THREE.Vector3()) };
        for (const [name, brightness, opacity] of [["arc-core", 1.8, 1], ["arc-halo", 0.9, 0.14]]) {
          const filament = new THREE.InstancedMesh(segment, new THREE.MeshBasicMaterial({
            color: new THREE.Color(PALETTE.softWhite).multiplyScalar(brightness),
            transparent: true, opacity, depthWrite: false, blending: THREE.AdditiveBlending,
          }), ARC_SEGMENTS);
          filament.name = name;
          filament.frustumCulled = false;
          filament.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
          arc.add(filament);
        }
        this.arcGroup.add(arc);
        return arc;
      });
    }
  }

  // Charge before contact, hold at the center, release before verification.
  activationEnvelope(travel) {
    const { smoothstep } = THREE.MathUtils;
    return {
      charge: smoothstep(travel, -3, -0.6) * (1 - smoothstep(travel, 0.8, 2.2)),
      link: smoothstep(travel, ...LINK_IN) * (1 - smoothstep(travel, ...LINK_OUT)),
    };
  }

  updateArc(arc, strength, time) {
    const points = arc.userData.points;
    const start = points[0];
    const end = points[ARC_SEGMENTS];
    this.arcGroup.worldToLocal(arc.userData.emitter.getWorldPosition(start));
    this.arcGroup.worldToLocal(arc.userData.antenna.getWorldPosition(end));
    for (let index = 1; index < ARC_SEGMENTS; index += 1) {
      const t = index / ARC_SEGMENTS;
      const bend = Math.sin(t * Math.PI);
      points[index].lerpVectors(start, end, t);
      // Fine, flowing angular detail instead of large random vertical shocks.
      points[index].y += bend * (0.14 + 0.065 * Math.sin(index * 2.7 + time * 8));
      points[index].z += bend * 0.07 * Math.cos(index * 3.9 - time * 7);
    }
    const [core, halo] = arc.children;
    core.material.opacity = strength;
    halo.material.opacity = strength * 0.14;
    const pose = this.arcPose;
    for (let index = 0; index < ARC_SEGMENTS; index += 1) {
      const length = this.arcDirection.subVectors(points[index + 1], points[index]).length();
      pose.position.copy(points[index]).add(points[index + 1]).multiplyScalar(0.5);
      pose.quaternion.setFromUnitVectors(UP, this.arcDirection.normalize());
      pose.scale.set(0.012, length, 0.012);
      pose.updateMatrix();
      core.setMatrixAt(index, pose.matrix);
      pose.scale.set(0.035, length, 0.035);
      pose.updateMatrix();
      halo.setMatrixAt(index, pose.matrix);
    }
    core.instanceMatrix.needsUpdate = true;
    halo.instanceMatrix.needsUpdate = true;
  }

  updateFaceDisplay(runner) {
    // The check is the robot's reaction to the second strike: it appears the
    // instant that bolt reaches full strength, not when it lets go (that was
    // most of a second of a landed bolt with no reaction). It belongs to the
    // pass the strike happened on: the delayed clock wraps 1.2 s after the
    // robot does, and in that window a robot re-entering at the far end
    // still carried the check. A robot behind the station is on a new pass
    // and shows its logo.
    const verified = runner.delayedTravel >= LINK_IN[1] && runner.group.position.z >= LINK_IN[1];
    const texture = verified ? this.checkTexture : runner.logoTexture;
    runner.display.visible = Boolean(texture);
    if (texture && runner.displayMaterial.map !== texture) {
      runner.displayMaterial.map = texture;
      runner.displayMaterial.emissiveMap = texture;
      runner.displayMaterial.needsUpdate = true;
    }
    // A brand glyph lights itself (white emissive x its own texture colour);
    // the check is a white drawing that takes the verified green instead.
    runner.displayMaterial.emissive.copy(verified ? PALETTE.verified : PALETTE.glyph);
    runner.displayMaterial.emissiveIntensity = verified ? 3.2 : 3.4;
  }

  updateFade(runner, travel) {
    applyFade(runner, riderOpacity(travel));
  }

  // The console and its pipes are set dressing at the edge of the shot: the
  // packets still travel, so the sequence reads, but the console's signal
  // lamp and the overhead lamp hold steady. Nothing outside the robots and
  // the coil tops moves on its own.
  updateOrchestrator() {
    const { smoothstep } = THREE.MathUtils;
    for (const flow of this.messageFlows) {
      const [start, end] = flow.direction === "inbound" ? [-3, -0.6] : [1.2, 3.6];
      const runner = this.robotRunners.find((r) => r.delayedTravel >= start && r.delayedTravel <= end);
      flow.packet.visible = Boolean(runner);
      if (!runner) continue;
      // Use the same position envelope as charging and arc release, so the
      // command/activation/acknowledgement sequence cannot drift over loops.
      const progress = (runner.delayedTravel - start) / (end - start);
      flow.packet.userData.progress = progress;
      flow.curve.getPointAt(progress, flow.packet.position);
      flow.curve.getTangentAt(progress, flow.tangent);
      flow.packet.quaternion.setFromUnitVectors(UP, flow.tangent);
      flow.packet.material.opacity = smoothstep(progress, 0, 0.1) * (1 - smoothstep(progress, 0.9, 1));
    }
  }

  // Sampling the same clock in the past gives a real delay even while the
  // conveyor eases through the station. Speed stays continuous at the wrap.
  conveyorOffset(elapsed = this.elapsed) {
    const step = elapsed / 4;
    const progress = step - 0.4 * Math.sin(step * Math.PI * 2) / (Math.PI * 2);
    return progress / this.robotRunners.length;
  }

  updateRobots() {
    const coilCharge = [0, 0];
    const offset = this.conveyorOffset();
    const delayedOffset = this.conveyorOffset(this.elapsed - COIL_DELAY);
    for (const runner of this.robotRunners) {
      const travel = ((offset + runner.phase + 0.5) % 1) * TRAVEL * 2 - TRAVEL;
      runner.delayedTravel = ((delayedOffset + runner.phase + 0.5) % 1) * TRAVEL * 2 - TRAVEL;
      runner.group.position.set(0, runner.height, travel);
      this.updateFade(runner, travel);

      // Keep the agent logo during scanning; the green check replaces it on exit.
      const scanCenter = (runner.rear - runner.front) / 2;
      const scanRadius = (runner.rear + runner.front) / 2;
      const inside = 1 - Math.min(1, Math.abs(travel - scanCenter) / scanRadius);
      this.updateFaceDisplay(runner);
      // Scanning brightens the face without washing the brand colour out.
      runner.displayMaterial.emissiveIntensity += inside * 0.8;
      runner.group.updateWorldMatrix(true, true);
      runner.arcs.forEach((arc, side) => {
        const { charge, link } = this.activationEnvelope(side === 0 ? travel : runner.delayedTravel);
        coilCharge[side] = Math.max(coilCharge[side], charge);
        arc.visible = link > 0;
        if (arc.visible) this.updateArc(arc, link, this.elapsed);
      });
    }
    for (const { material, level, side } of this.coilIndicators) {
      material.emissiveIntensity = 0.12 + 1.1 * THREE.MathUtils.smoothstep(coilCharge[side], level, level + 0.2);
    }
    this.updateBelt();
    this.updateOrchestrator();
  }

  schedule() {
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.lastTime = 0;
    this.governor?.reset();
    if (this.started && !this.paused && this.visible && !document.hidden) {
      this.frame = requestAnimationFrame((time) => this.render(time));
    }
  }

  resize() {
    const { width, height } = this.container.getBoundingClientRect();
    if (!width || !height) return;
    this.camera.aspect = width / height;
    // The split layout frames the belt on the right of the copy; the stacked
    // one puts it in the band under the buttons. Keyed on the LAYOUT, not the
    // aspect: on a short phone that band is wider than tall, and the split's
    // pulled-back camera made the belt a small model in the middle of it.
    if (!SPLIT_LAYOUT.matches) {
      // A diagonal, not a close-up: the belt enters past the lower-left edge
      // and leaves just beyond the top-right corner, with its whole width in
      // frame. The camera looks down steeply from the belt's side and never
      // rolls, because a roll on top of perspective read as a skewed, badly
      // angled shot. Solved numerically by projecting the belt's geometry
      // for aspects 0.9 and 1.4, which converge on this one pose.
      this.camera.fov = 36;
      this.camera.position.set(9.6, 17.8, 7.2);
      this.camera.lookAt(1, 1.2, 0);
      this.camera.clearViewOffset();
    } else {
      this.camera.fov = 34;
      this.camera.position.set(14, 10.5, 17);
      this.camera.lookAt(0.4, 0.2, -0.4);
      // Slide the picture toward the top-right corner without re-aiming the
      // camera: a view offset moves the projection window, so the perspective
      // and the diagonal clip's clearance on the left stay as tuned.
      this.camera.setViewOffset(width, height, -width * 0.12, height * 0.05, width, height);
    }
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.composer.setPixelRatio(this.renderer.getPixelRatio());
    this.composer.setSize(width, height);
    this.composer.render();
  }

  render(time) {
    this.frame = null;
    if (this.paused || !this.visible || document.hidden) return;
    this.frame = requestAnimationFrame((next) => this.render(next));
    if (!this.governor.shouldRender(time)) return;
    const delta = this.lastTime ? Math.min((time - this.lastTime) / 1000, 0.06) : 0;
    this.lastTime = time;
    this.elapsed += delta;
    this.updateRobots();
    this.gpuTimer.begin();
    this.composer.render();
    this.gpuTimer.end();
    const gpu = this.gpuTimer.poll();
    this.governor.measure(gpu);
    this.stats.frames += 1;
    if (gpu.length) this.stats.gpuMs = +gpu.at(-1).toFixed(2);
    const quality = this.governor.record(time);
    // A quality change reallocates buffers, which costs one long frame.
    // Never spend it while an arc is firing: that is the moment people are
    // watching, and a hitch there is what "stutter" means on this page.
    if (quality !== null) this.pendingQuality = quality;
    if (this.pendingQuality && !this.arcFiring()) {
      this.applyQuality(this.pendingQuality);
      this.pendingQuality = null;
    }
  }

  arcFiring() {
    return this.robotRunners.some((runner) => runner.arcs.some((arc) => arc.visible));
  }

  applyQuality(quality) {
    const { pixelRatio, samples, shadow } = quality;
    // A target's sample count is fixed at allocation; disposing it makes the
    // next render reallocate with the new count. The shadow map likewise.
    if (this.sceneTarget.samples !== samples) {
      this.sceneTarget.samples = samples;
      this.sceneTarget.dispose();
    }
    if (this.keyLight.shadow.mapSize.x !== shadow) {
      this.keyLight.shadow.mapSize.set(shadow, shadow);
      this.keyLight.shadow.map?.dispose();
      this.keyLight.shadow.map = null;
    }
    this.renderer.setPixelRatio(pixelRatio);
    this.stats.quality = { ...quality };
    this.stats.changes += 1;
    this.resize();
  }
}

// SwiftShader, llvmpipe and friends rasterize on the CPU, where this scene
// runs at a few frames a second at full quality. Chrome only names the real
// renderer through the debug extension; elsewhere RENDERER is the best hint.
function isSoftwareRenderer(gl) {
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  const name = gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER) || "";
  return /swiftshader|llvmpipe|softpipe|software|basic render/i.test(name);
}
