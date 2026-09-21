import * as THREE from "./vendor/three.module.js";
import { EffectComposer } from "./vendor/postprocessing/EffectComposer.js";
import { RenderPass } from "./vendor/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "./vendor/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "./vendor/postprocessing/OutputPass.js";

const LANDING_SELECTOR = "[data-grove-landing]";
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
// Mirrors the stylesheet's breakpoint: above it the scene is the right column
// of a split hero, below it a band under the copy.
const SPLIT_LAYOUT = window.matchMedia("(min-width: 901px)");

// Robots travel toward the camera along +z. The scanner sits at z = 0, so
// everything before it is "incoming" (cyan) and everything after is "verified"
// (green checkbox on the face display).
const BELT_LENGTH = 34;
const TRAVEL = BELT_LENGTH / 2 - 3;
// The warm side is the docs theme's clay accent, not the reference's magenta:
// the page around the canvas is built on that hue, and one hero with a pink
// lighting rig sitting beside terracotta buttons read as two design systems.
const PALETTE = {
  night: 0x04060d,
  incoming: new THREE.Color(0x3fd8ff),
  glyph: new THREE.Color(0xffffff),
  verified: new THREE.Color(0x37d67f),
  ember: 0xff7a45,
  emberDeep: 0xe0562a,
  emberNeon: 0xffa072,
};

class FactoryScene {
  constructor(container) {
    this.container = container;
    this.canvas = container.querySelector("[data-grove-factory-canvas]");
    this.paused = REDUCED_MOTION.matches;
    this.lastTime = 0;
    this.elapsed = 0;
    this.robotRunners = [];
    this.frame = null;
    this.visible = true;
    this.started = false;
    this.textureLoads = [];
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.initialize();
  }

  async initialize() {
    try {
      this.build();
      await Promise.all(this.textureLoads);
      this.updateRobots();
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
        await this.canvas.animate([{ opacity: 0 }, { opacity: 1 }], {
          duration: 700,
          easing: "ease-out",
          fill: "forwards",
        }).finished;
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
    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: false, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.9;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(PALETTE.night);
    this.scene.fog = new THREE.Fog(PALETTE.night, 22, 46);
    this.scene.environment = this.makeEnvironment();

    this.camera = new THREE.PerspectiveCamera(30, 1, 0.1, 120);
    this.camera.position.set(10.5, 9.5, 16.5);
    this.camera.lookAt(0.6, 1.2, -0.5);

    this.addLights();
    this.addBelt();
    this.addScanner();
    this.addRobots();
    this.addAtmosphere();

    // Bloom is what makes the eyes, the pane and the neon edges read as light
    // sources instead of flat coloured paint.
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.26, 0.4, 0.97);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());
  }

  // Two-tone studio: cool white overhead, cyan on the scanner side, ember on
  // the near side. Chrome only looks like chrome when it has something to reflect.
  makeEnvironment() {
    const environment = new THREE.Scene();
    environment.background = new THREE.Color(0x0a1224);
    for (const [color, width, height, position] of [
      [0xf4f8ff, 18, 6, [0, 14, 0]],
      [0x3fb8ff, 6, 20, [-12, 4, -8]],
      [PALETTE.ember, 6, 20, [12, 2, 10]],
      [0x8ab4ff, 24, 4, [0, 3, -16]],
    ]) {
      const card = new THREE.Mesh(new THREE.PlaneGeometry(width, height), new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide }));
      card.position.fromArray(position);
      card.lookAt(0, 0, 0);
      environment.add(card);
    }
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const target = pmrem.fromScene(environment, 0.05);
    pmrem.dispose();
    environment.traverse((object) => {
      if (object.isMesh) { object.geometry.dispose(); object.material.dispose(); }
    });
    return target.texture;
  }

  addLights() {
    this.scene.add(new THREE.HemisphereLight(0x8fb4ff, 0x1a0c08, 0.5));
    const key = new THREE.DirectionalLight(0xf0f4ff, 1.8);
    key.position.set(-6, 14, 6);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.left = -14;
    key.shadow.camera.right = 14;
    key.shadow.camera.top = 16;
    key.shadow.camera.bottom = -16;
    key.shadow.bias = -0.0008;
    this.scene.add(key);
    // Fill from the camera's side of the belt. Every other source sits above
    // or behind the robots, so the faces the viewer actually sees had nothing
    // to reflect and read as black. Directional and wide rather than a point
    // light, so it lifts the whole queue evenly and adds no hotspot.
    const fill = new THREE.DirectionalLight(0xdfe8ff, 0.7);
    fill.position.set(12, 6, 18);
    this.scene.add(fill);
    this.addPointLight(0x3fb8ff, 10, [-4, 5, -7], 22);
    this.addPointLight(PALETTE.ember, 12, [4, 2.2, 8], 20);
    this.addPointLight(0xffb27a, 4, [7, 0.5, -3], 18);
  }

  addPointLight(color, intensity, position, distance = 16) {
    const light = new THREE.PointLight(color, intensity, distance, 1.6);
    light.position.fromArray(position);
    this.scene.add(light);
    return light;
  }

  material(color, metalness = 0, roughness = 0.5, emissive = 0x000000, emissiveIntensity = 0) {
    return new THREE.MeshStandardMaterial({ color, metalness, roughness, emissive, emissiveIntensity });
  }

  roundedBox(width, height, depth, radius, material, segments = 5) {
    const shape = new THREE.Shape();
    const x = -width / 2;
    const y = -height / 2;
    shape.moveTo(x + radius, y);
    shape.lineTo(x + width - radius, y);
    shape.quadraticCurveTo(x + width, y, x + width, y + radius);
    shape.lineTo(x + width, y + height - radius);
    shape.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    shape.lineTo(x + radius, y + height);
    shape.quadraticCurveTo(x, y + height, x, y + height - radius);
    shape.lineTo(x, y + radius);
    shape.quadraticCurveTo(x, y, x + radius, y);
    const geometry = new THREE.ExtrudeGeometry(shape, {
      depth: Math.max(depth - radius * 0.7, 0.01),
      bevelEnabled: true,
      bevelThickness: radius * 0.35,
      bevelSize: radius * 0.33,
      bevelSegments: segments,
      curveSegments: 12,
    });
    geometry.center();
    return new THREE.Mesh(geometry, material);
  }

  // One perforated steel panel, tiled along the belt. Drawing it is cheaper
  // than instancing ~4000 holes and gives the brushed grain for free.
  makePanelTextures() {
    const size = 512;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const context = canvas.getContext("2d");
    const grain = context.createLinearGradient(0, 0, size, size * 0.3);
    grain.addColorStop(0, "#cfd9e8");
    grain.addColorStop(0.5, "#aebbcf");
    grain.addColorStop(1, "#c4cfdf");
    context.fillStyle = grain;
    context.fillRect(0, 0, size, size);
    for (let index = 0; index < 900; index += 1) {
      context.fillStyle = `rgba(255,255,255,${Math.random() * 0.06})`;
      context.fillRect(0, Math.random() * size, size, 1);
      context.fillStyle = `rgba(20,30,50,${Math.random() * 0.07})`;
      context.fillRect(0, Math.random() * size, size, 1);
    }
    const holes = 22;
    const step = size / holes;
    for (let row = 0; row < holes; row += 1) {
      for (let column = 0; column < holes; column += 1) {
        const cx = (column + 0.5) * step;
        const cy = (row + 0.5) * step;
        context.beginPath();
        context.arc(cx, cy, step * 0.24, 0, Math.PI * 2);
        context.fillStyle = "#0a1020";
        context.fill();
        context.beginPath();
        context.arc(cx + step * 0.05, cy + step * 0.05, step * 0.17, 0, Math.PI * 2);
        context.fillStyle = "#20304d";
        context.fill();
      }
    }
    context.lineWidth = 10;
    context.strokeStyle = "#141c2d";
    context.strokeRect(0, 0, size, size);
    context.lineWidth = 3;
    context.strokeStyle = "rgba(255,255,255,0.35)";
    context.strokeRect(9, 9, size - 18, size - 18);
    const map = new THREE.CanvasTexture(canvas);
    map.colorSpace = THREE.SRGBColorSpace;
    map.wrapS = map.wrapT = THREE.RepeatWrapping;
    map.anisotropy = this.renderer.capabilities.getMaxAnisotropy();

    const rough = document.createElement("canvas");
    rough.width = size;
    rough.height = size;
    const roughContext = rough.getContext("2d");
    roughContext.fillStyle = "#4a4a4a";
    roughContext.fillRect(0, 0, size, size);
    for (let row = 0; row < holes; row += 1) {
      for (let column = 0; column < holes; column += 1) {
        roughContext.beginPath();
        roughContext.arc((column + 0.5) * step, (row + 0.5) * step, step * 0.24, 0, Math.PI * 2);
        roughContext.fillStyle = "#e0e0e0";
        roughContext.fill();
      }
    }
    const roughnessMap = new THREE.CanvasTexture(rough);
    roughnessMap.wrapS = roughnessMap.wrapT = THREE.RepeatWrapping;
    return { map, roughnessMap };
  }

  addBelt() {
    const belt = new THREE.Group();
    belt.position.set(0.3, -1.1, 0);
    belt.rotation.y = 0.2;
    this.scene.add(belt);
    this.belt = belt;

    const width = 8.5;
    const { map, roughnessMap } = this.makePanelTextures();
    map.repeat.set(2, Math.round(BELT_LENGTH / (width / 2)));
    roughnessMap.repeat.copy(map.repeat);
    const steel = new THREE.MeshStandardMaterial({ color: 0xffffff, map, roughnessMap, metalness: 0.85, roughness: 1, envMapIntensity: 0.9 });
    const surface = new THREE.Mesh(new THREE.BoxGeometry(width, 0.7, BELT_LENGTH), steel);
    surface.receiveShadow = true;
    surface.castShadow = true;
    belt.add(surface);

    // Chrome side rails plus a neon underglow strip: the warm light along the
    // near edge of the reference belt, in the docs' clay rather than magenta.
    const rail = this.material(0xcfd8e6, 1, 0.18);
    for (const side of [-1, 1]) {
      const edge = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.9, BELT_LENGTH + 0.2), rail);
      edge.position.set(side * (width / 2 + 0.1), -0.05, 0);
      edge.castShadow = true;
      belt.add(edge);
      const neon = new THREE.Mesh(
        new THREE.BoxGeometry(0.08, 0.1, BELT_LENGTH),
        this.material(side > 0 ? PALETTE.emberNeon : 0x52d8ff, 0, 0.4, side > 0 ? PALETTE.emberDeep : 0x2ec9ff, 1.1)
      );
      neon.position.set(side * (width / 2 + 0.24), -0.42, 0);
      belt.add(neon);
    }
    const underglow = new THREE.PointLight(PALETTE.emberDeep, 6, 12, 1.6);
    underglow.position.set(width / 2 + 1.5, -1.6, 4);
    belt.add(underglow);
    const underglowFar = new THREE.PointLight(0x2ec9ff, 4, 12, 1.6);
    underglowFar.position.set(-width / 2 - 1.5, -1.6, -6);
    belt.add(underglowFar);
  }

  addScanner() {
    const scanner = new THREE.Group();
    scanner.position.set(0, 0.35, 0);
    this.belt.add(scanner);
    const chrome = this.material(0xe6ecf5, 1, 0.12);
    const darkChrome = this.material(0x2b3648, 0.95, 0.25);
    const glass = new THREE.MeshPhysicalMaterial({
      color: 0x69e0ff,
      metalness: 0,
      roughness: 0.24,
      transmission: 0.7,
      thickness: 0.4,
      transparent: true,
      opacity: 0.55,
      emissive: 0x2fc4ff,
      emissiveIntensity: 0.35,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    this.scannerGlass = glass;

    const span = 4.2;
    for (const x of [-span, span]) {
      const post = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 4.6, 40), chrome);
      post.position.set(x, 2.3, 0);
      post.castShadow = true;
      scanner.add(post);
      const cap = new THREE.Mesh(new THREE.SphereGeometry(0.21, 32, 20), chrome);
      cap.position.set(x, 4.6, 0);
      scanner.add(cap);
      const foot = new THREE.Mesh(new THREE.CylinderGeometry(0.44, 0.5, 0.2, 40), darkChrome);
      foot.position.set(x, 0.1, 0);
      scanner.add(foot);
    }
    const sheet = new THREE.Mesh(new THREE.PlaneGeometry(span * 2 - 0.3, 4.1), glass);
    sheet.position.set(0, 2.45, 0);
    scanner.add(sheet);

    // The lamp lives above the pane, not in it. At the robots' antenna height
    // it sat centimetres from the chrome knobs as each passed, and a point
    // light that close to a mirror is a white hotspot the bloom turns into a
    // flare, one that pulsed with the scan and read as flicker.
    this.scannerLight = new THREE.PointLight(0x49dcff, 2.4, 11, 1.8);
    this.scannerLight.position.set(0, 5.4, 0.4);
    scanner.add(this.scannerLight);
  }

  makeCheckTexture() {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 256;
    const context = canvas.getContext("2d");
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
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }

  addRobots() {
    this.checkTexture = this.makeCheckTexture();
    const badges = ["claude-code.svg", "codex.svg", "opencode.svg"];
    const robotCount = 5;
    this.robotRunners = Array.from({ length: robotCount }, (_, index) => ({
      phase: index / robotCount,
      badge: badges[index % badges.length],
    }));
    for (const runner of this.robotRunners) {
      runner.group = this.makeRobot(runner);
      this.applyRobotBadge(runner);
      this.belt.add(runner.group);
    }
  }

  applyRobotBadge(spec) {
    const loader = new THREE.TextureLoader();
    // Resolve against this module, not the page: the scene is reachable from
    // any route and a page-relative path breaks on all but one of them.
    const glyph = new URL(`../logos/agent-displays/${spec.badge}`, import.meta.url).href;
    this.textureLoads.push(loader.loadAsync(glyph).then((texture) => {
      texture.colorSpace = THREE.SRGBColorSpace;
      spec.logoTexture = texture;
    }));
  }

  updateFaceDisplay(runner) {
    // The shell extends 1.1 units behind its centre: switch once it clears
    // the pane, and restore the agent logo when the conveyor loop wraps.
    const verified = runner.group.position.z >= 1.2;
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

  makeRobot(spec) {
    const group = new THREE.Group();
    // Painted metal, not a mirror. A near-full metalness shell has almost no
    // diffuse term, so it can only show what it reflects, and the studio's
    // cards all sit above and behind the queue: every camera-facing side was
    // black however bright the rig. Diffuse response is what makes a fill
    // light land on it without adding gloss.
    const body = this.material(0x2a3a60, 0.55, 0.42);
    body.envMapIntensity = 1.0;
    // Brushed rather than polished: a mirror knob reflects the whole studio
    // as one white point and the bloom pass lifts every such point into a star.
    const chrome = this.material(0xd6dde8, 1, 0.34);
    const screen = this.material(0x03060e, 0.6, 0.12);
    const displayMaterial = new THREE.MeshStandardMaterial({ color: 0x000000, emissive: PALETTE.glyph.clone(), emissiveIntensity: 2.6, roughness: 0.2, transparent: true });
    spec.displayMaterial = displayMaterial;
    spec.bodyMaterial = body;

    const shell = this.roundedBox(2.2, 2.05, 2.2, 0.5, body, 6);
    shell.castShadow = true;
    shell.receiveShadow = true;
    group.add(shell);

    // The glyph sits flush on the recessed screen, not on a camera-facing sprite.
    const bezel = this.roundedBox(1.72, 1.16, 0.12, 0.3, this.material(0x33456e, 0.55, 0.45));
    bezel.position.set(0, 0.08, 1.18);
    group.add(bezel);
    const face = this.roundedBox(1.56, 1.0, 0.1, 0.26, screen);
    face.position.set(0, 0.08, 1.24);
    group.add(face);
    const display = new THREE.Mesh(new THREE.PlaneGeometry(0.78, 0.78), displayMaterial);
    display.position.set(0, 0.08, 1.35);
    // Hide the untextured plane while its local glyph loads.
    display.visible = false;
    group.add(display);
    spec.display = display;

    // Twin chrome antennas with knobs, like the front robot in the reference.
    for (const x of [-0.55, 0.55]) {
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.08, 0.55, 16), chrome);
      stem.position.set(x, 1.36, 0.2);
      group.add(stem);
      const knob = new THREE.Mesh(new THREE.SphereGeometry(0.14, 20, 16), chrome);
      knob.position.set(x, 1.66, 0.2);
      group.add(knob);
      const socket = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.16, 0.08, 16), chrome);
      socket.position.set(x, 1.12, 0.2);
      group.add(socket);
    }

    // Inset side panels give the flanks the paneled look of the reference.
    for (const side of [-1, 1]) {
      const panel = this.roundedBox(1.6, 1.5, 0.08, 0.35, this.material(0x2f4068, 0.55, 0.45));
      panel.position.set(side * 1.19, 0, -0.1);
      panel.rotation.y = side * Math.PI / 2;
      group.add(panel);
    }
    return group;
  }

  addAtmosphere() {
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(120, 120), this.material(0x05070f, 0.2, 0.9));
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -1.9;
    floor.receiveShadow = true;
    this.scene.add(floor);
    const grid = new THREE.GridHelper(120, 60, 0x1b2a4d, 0x111a30);
    grid.position.y = -1.88;
    this.scene.add(grid);
  }

  schedule() {
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.lastTime = 0;
    if (this.started && !this.paused && this.visible && !document.hidden) {
      this.frame = requestAnimationFrame((time) => this.render(time));
    }
  }

  resize() {
    const { width, height } = this.container.getBoundingClientRect();
    if (!width || !height) return;
    this.camera.aspect = width / height;
    // Landscape frames the belt on the right of the copy; portrait puts the
    // scanner in the lower two thirds under the headline.
    if (this.camera.aspect < 1) {
      this.camera.fov = 46;
      this.camera.position.set(8, 8, 12.5);
      this.camera.lookAt(0.2, 1.6, 0.8);
      this.camera.clearViewOffset();
    } else {
      this.camera.fov = 34;
      this.camera.position.set(14, 10.5, 17);
      this.camera.lookAt(0.4, 0.2, -0.4);
      // Slide the picture toward the top-right corner without re-aiming the
      // camera: a view offset moves the projection window, so the perspective
      // and the diagonal clip's clearance on the left stay as tuned. The
      // canvas is the 62% split column (aspect ~1.1 on any 16:9 monitor), and
      // in it the belt sat centre-left with a column of empty background
      // against the page edge. Stacked phone layouts keep the centred frame.
      if (SPLIT_LAYOUT.matches) {
        this.camera.setViewOffset(width, height, -width * 0.12, height * 0.05, width, height);
      } else {
        this.camera.clearViewOffset();
      }
    }
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.composer.setSize(width, height);
    this.composer.render();
  }

  conveyorOffset() {
    // One clock keeps the queue evenly spaced. Sinusoidal easing slows the
    // belt at the gate without stopping; speed stays continuous across cycles.
    const step = this.elapsed / 4;
    const progress = step - 0.4 * Math.sin(step * Math.PI * 2) / (Math.PI * 2);
    return progress / this.robotRunners.length;
  }

  updateRobots() {
    let insideScanner = 0;
    const offset = this.conveyorOffset();
    for (const runner of this.robotRunners) {
      const cycle = (offset + runner.phase + 0.5) % 1;
      const travel = cycle * TRAVEL * 2 - TRAVEL;
      runner.group.position.set(0, 1.5, travel);

      // Keep the agent logo during scanning; the green check replaces it on exit.
      const inside = 1 - Math.min(1, Math.abs(travel) / 1.4);
      insideScanner = Math.max(insideScanner, inside);
      this.updateFaceDisplay(runner);
      // Scanning brightens the face without washing the brand colour out.
      runner.displayMaterial.emissiveIntensity += inside * 0.8;
    }
    this.scannerGlass.emissiveIntensity = 0.3 + insideScanner * 0.1;
    this.scannerLight.intensity = 2.4 + insideScanner * 0.8;
  }

  render(time) {
    this.frame = null;
    if (this.paused || !this.visible || document.hidden) return;
    const delta = this.lastTime ? Math.min((time - this.lastTime) / 1000, 0.06) : 0;
    this.lastTime = time;
    this.elapsed += delta;
    this.updateRobots();
    this.composer.render();
    this.frame = requestAnimationFrame((next) => this.render(next));
  }
}

const landing = document.querySelector(LANDING_SELECTOR);
if (landing) {
  const scene = landing.querySelector(".grove-factory-scene");
  if (scene instanceof HTMLElement) new FactoryScene(scene);
}
