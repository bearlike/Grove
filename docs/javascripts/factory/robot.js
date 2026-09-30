import * as THREE from "../vendor/three.module.js";
import { FADE_SPAN, PALETTE, TRAVEL } from "./palette.js";
import { box, circle, cylinder, discStack, fasteners, material, roundedBox, setShadows, torus } from "./parts.js";

// One courier robot: a rounded shell with a chrome face bezel, a display that
// shows the agent's mark until the coils verify it, two antennae the
// activation arcs land on, and a rubber track either side.
//
// `spec` is the runner's own record. The robot writes the handles the
// choreography needs back onto it: display, displayMaterial, antennaTips,
// bodyMaterial and fadeMaterials.
export function buildRobot(spec) {
  const group = new THREE.Group();
  // Every material is this robot's own instance, so fading one robot never
  // fades its neighbours. Names are stable roles: the baker shares merged
  // geometry between robots by role, since every robot is built identically.
  const body = named(material(0xc5c3c0, 0.38, 0.3), "robot-body");
  const chrome = named(material(0xdcdad8, 0.85, 0.32), "robot-chrome");
  const graphite = named(material(0x252423, 0.45, 0.46), "robot-graphite");
  const rubber = named(material(0x242423, 0.05, 0.88), "robot-rubber");
  const glow = named(material(PALETTE.softWhite, 0.15, 0.35, PALETTE.warmWhite, 0.65), "robot-glow");
  const screen = named(new THREE.MeshPhysicalMaterial({ color: 0x080808, metalness: 0.18, roughness: 0.16, clearcoat: 1, clearcoatRoughness: 0.12 }), "robot-screen");
  const displayMaterial = new THREE.MeshStandardMaterial({ color: 0x000000, emissive: PALETTE.glyph.clone(), emissiveIntensity: 2.6, roughness: 0.2, transparent: true });

  const place = (mesh, x, y, z, name) => {
    mesh.position.set(x, y, z);
    if (name) mesh.name = name;
    group.add(mesh);
    return mesh;
  };

  // Casing, front to back. A dark reveal between two separate casings
  // survives lighting changes; painted lines on one box do not describe how
  // a machine is assembled.
  place(roundedBox(2.25, 2.18, 2.08, 0.48, body, 6), 0, 0.18, 0);
  place(roundedBox(2.29, 2.22, 0.08, 0.48, graphite), 0, 0.18, 0.76, "shell-seam");
  place(roundedBox(2.27, 2.2, 0.32, 0.47, chrome, 6), 0, 0.18, 0.99);
  place(roundedBox(1.99, 1.91, 0.08, 0.39, graphite), 0, 0.24, 1.19);
  place(roundedBox(1.91, 1.83, 0.06, 0.37, glow), 0, 0.24, 1.235, "screen-halo");
  place(roundedBox(1.81, 1.73, 0.075, 0.34, screen, 6), 0, 0.24, 1.285);
  const display = place(new THREE.Mesh(new THREE.PlaneGeometry(1.03, 1.03), displayMaterial), 0, 0.24, 1.335, "display");
  // Never flash an untextured display: the choreography shows it once a
  // texture is assigned.
  display.visible = false;

  spec.antennaTips = [-0.62, 0.62].map((x) => {
    const antenna = discStack([
      [0.16, 0.12, 0, graphite], [0.09, 0.29, 0.16, chrome],
      [0.145, 0.3, 0.4, chrome], [0.15, 0.035, 0.53, graphite],
      [0.15, 0.065, 0.58, body],
    ], 24);
    antenna.position.set(x, 1.31, 0.1);
    antenna.rotation.z = -Math.sign(x) * 0.14;
    // The point an activation arc lands on, tracked in world space per frame.
    const tip = new THREE.Object3D();
    tip.name = "antenna-tip";
    tip.position.y = 0.61;
    antenna.add(tip);
    group.add(antenna);
    return tip;
  });

  const bolts = [];
  for (const side of [-1, 1]) {
    const ear = discStack([
      [0.26, 0.09, 0, graphite], [0.22, 0.12, side * 0.055, chrome],
      [0.155, 0.13, side * 0.08, body],
    ], 32, "x");
    ear.name = "ear";
    ear.position.set(side * 1.19, 0.62, -0.02);
    group.add(ear);
    place(roundedBox(0.65, 0.19, 1.46, 0.07, body), side * 1.32, 0.12, -0.05);
    const panel = place(roundedBox(1.45, 0.98, 0.13, 0.18, graphite), side * 1.58, -0.28, -0.05, "service-panel");
    panel.rotation.y = side * Math.PI / 2;
    const lid = place(roundedBox(1.34, 0.87, 0.08, 0.14, body), side * 1.675, -0.28, -0.05);
    lid.rotation.copy(panel.rotation);
    for (let index = 0; index < 4; index += 1) {
      place(new THREE.Mesh(box(0.018, 0.22, 0.055), graphite), side * 1.72, -0.28, -0.3 + index * 0.16);
    }
    for (const z of [-0.59, 0.49]) bolts.push({ position: [side * 1.73, -0.28, z], rotation: [0, side * Math.PI / 2, 0] });
    group.add(buildTrack(side, rubber, graphite, chrome));
  }
  for (const x of [-0.99, 0.99]) {
    for (const y of [-0.66, 1.05]) bolts.push({ position: [x, y, 1.18], rotation: [0, 0, 0] });
  }
  group.add(fasteners(bolts, chrome, graphite));
  for (const x of [-0.37, 0.37]) {
    place(new THREE.Mesh(torus(0.055, 0.017, 8, 20), chrome), x, -0.8, 1.175);
    place(new THREE.Mesh(circle(0.043, 20), graphite), x, -0.8, 1.175);
  }
  setShadows(group, true, true, display);

  Object.assign(spec, {
    display,
    displayMaterial,
    bodyMaterial: body,
    fadeMaterials: [body, chrome, graphite, rubber, glow, screen, displayMaterial],
  });
  return group;
}

function named(surface, name) {
  surface.name = name;
  return surface;
}

// A rubber track: a rounded tyre, one instanced draw for its ribs laid along
// the tyre's own stadium outline, and two hubs.
function buildTrack(side, rubber, graphite, chrome) {
  const track = new THREE.Group();
  track.name = "track";
  track.position.set(side * 1.32, -0.83, -0.05);
  const tire = roundedBox(2.65, 1.02, 0.58, 0.49, rubber);
  tire.rotation.y = Math.PI / 2;
  track.add(tire);

  const outline = new THREE.CurvePath();
  outline.add(new THREE.LineCurve(new THREE.Vector2(-0.815, -0.525), new THREE.Vector2(0.815, -0.525)));
  outline.add(new THREE.EllipseCurve(0.815, 0, 0.525, 0.525, -Math.PI / 2, Math.PI / 2));
  outline.add(new THREE.LineCurve(new THREE.Vector2(0.815, 0.525), new THREE.Vector2(-0.815, 0.525)));
  outline.add(new THREE.EllipseCurve(-0.815, 0, 0.525, 0.525, Math.PI / 2, Math.PI * 1.5));
  const treads = new THREE.InstancedMesh(box(0.64, 0.09, 0.15), rubber, 44);
  treads.name = "treads";
  const pose = new THREE.Object3D();
  for (let index = 0; index < treads.count; index += 1) {
    const point = outline.getPointAt(index / treads.count);
    const tangent = outline.getTangentAt(index / treads.count);
    pose.position.set(0, point.y, point.x);
    pose.rotation.x = -Math.atan2(tangent.y, tangent.x);
    pose.updateMatrix();
    treads.setMatrixAt(index, pose.matrix);
  }
  track.add(treads);

  for (const z of [-0.73, 0.73]) {
    const hub = discStack([
      [0.43, 0.09, 0, graphite], [0.34, 0.1, side * 0.045, chrome],
      [0.25, 0.11, side * 0.07, graphite], [0.12, 0.13, side * 0.095, chrome],
    ], 32, "x");
    hub.name = "hub";
    hub.position.set(side * 0.34, 0, z);
    const ring = new THREE.Mesh(torus(0.365, 0.017, 8, 40), chrome);
    ring.rotation.y = Math.PI / 2;
    ring.position.x = side * 0.075;
    hub.add(ring);
    track.add(hub);
  }
  return track;
}

// Opacity as a smooth function of belt position: zero at either end of the
// run, one through the middle. Smoothstep so the fade has no visible kink.
export function riderOpacity(travel) {
  const edge = THREE.MathUtils.clamp(Math.min(travel + TRAVEL, TRAVEL - travel) / FADE_SPAN, 0, 1);
  return edge * edge * (3 - 2 * edge);
}

// Apply a fade to one robot. A robot is only TRANSPARENT while it is fading:
// transparent draws skip early depth rejection and are re-sorted every frame,
// and for most of its run a robot is simply solid.
export function applyFade(runner, opacity) {
  const fading = opacity < 1;
  for (const surface of runner.fadeMaterials) {
    surface.opacity = opacity;
    // The display is a glyph with its own alpha, so it always blends.
    if (surface !== runner.displayMaterial && surface.transparent !== fading) {
      surface.transparent = fading;
      surface.needsUpdate = true;
    }
  }
  // A shadow has no alpha, so a shell at 5% opacity would still drop a full
  // shadow on the deck and that shadow would be the pop the fade removes.
  const casts = opacity > 0.5;
  if (runner.castsShadow !== casts) {
    runner.castsShadow = casts;
    runner.group.traverse((part) => { if (part.isMesh && part !== runner.display) part.castShadow = casts; });
  }
  runner.fade = opacity;
}
