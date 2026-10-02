import * as THREE from "../vendor/three.module.js";
import { PALETTE } from "./palette.js";
import { cylinder, discStack, instanced, material, roundedBox, setShadows, torus } from "./parts.js";

// The activation station: two grounded coil pillars flanking the lane, and on
// the near side the orchestration console whose message pipes run off-frame.
// Returns the handles the choreography animates.
export function buildStation() {
  const station = new THREE.Group();
  station.name = "station";
  station.position.set(0, 0.35, 0);
  const shell = material(0xb7b4af, 0.5, 0.32);
  const graphite = material(0x282726, 0.5, 0.48);
  const steel = material(0x958a7c, 0.78, 0.34);
  const windingSteel = material(0x958a7c, 0.72, 0.46);

  const coilEmitters = [];
  const coilIndicators = [];
  for (const side of [-1, 1]) {
    const pillar = buildPillar(side, { shell, graphite, steel, windingSteel });
    station.add(pillar);
    coilEmitters.push(pillar.getObjectByName("coil-emitter"));
    for (const [index, band] of pillar.userData.bands.entries()) {
      coilIndicators.push({ material: band.material, level: index / 5, side: side < 0 ? 0 : 1 });
    }
  }
  // Soft overhead fill, kept above the pane and away from the reflective
  // antenna caps: at antenna height it lit the chrome knobs from centimetres
  // away and bloom turned that into a pulsing star.
  const scannerLight = new THREE.PointLight(PALETTE.warmWhite, 1.6, 11, 1.8);
  scannerLight.position.set(0, 6.1, 0.4);
  station.add(scannerLight);
  return { station, coilEmitters, coilIndicators, scannerLight, ...buildConsole(station) };
}

function buildPillar(side, { shell, graphite, steel, windingSteel }) {
  const pillar = new THREE.Group();
  pillar.name = "activation-pillar";
  pillar.position.x = side * 4.55;
  const add = (mesh, y, name) => {
    mesh.position.y = y;
    if (name) mesh.name = name;
    pillar.add(mesh);
    return mesh;
  };
  // The base is a plain cabinet: two boxes, no stencil, vents or trim. It
  // sits at the edge of what people look at, and the coil above it is what
  // the shot is about, so it costs as little as a base can.
  add(roundedBox(1.85, 0.3, 1.7, 0.12, graphite, 2), -2.22);
  add(roundedBox(1.45, 2.15, 1.35, 0.18, shell, 2), -0.995);
  pillar.add(discStack([
    [0.75, 0.22, 0.19, graphite], [0.65, 0.2, 0.4, shell],
    [0.39, 2.2, 1.6, graphite], [0.54, 0.16, 2.76, shell],
    [0.27, 0.24, 2.94, steel],
  ], 40));

  // The windings are the coil's structure and sit at the centre of the
  // shot: rounder rings (64 segments) and a satin rather than polished
  // finish, so under motion they read as grooves instead of a flicker of
  // sub-pixel highlights that no amount of antialiasing can hold still.
  const windings = instanced(torus(0.46, 0.03, 10, 64), windingSteel, 18, (pose, index) => {
    pose.rotation.x = Math.PI / 2;
    pose.position.y = 0.63 + index * 0.115;
  });
  windings.name = "coil-windings";
  pillar.add(windings);
  // Five indicator bands light bottom to top as the coil charges. Each has
  // its own material because each glows to its own level.
  pillar.userData.bands = Array.from({ length: 5 }, (_, index) => {
    const band = add(new THREE.Mesh(torus(0.48, 0.022, 10, 64), material(PALETTE.softWhite, 0.1, 0.4, PALETTE.warmWhite, 0.12)), 0.7 + index * 0.44);
    band.rotation.x = Math.PI / 2;
    return band;
  });
  add(new THREE.Mesh(torus(0.61, 0.17, 12, 48), shell), 3.18, "coil-crown").rotation.x = Math.PI / 2;
  add(new THREE.Mesh(cylinder(0.46, 0.46, 0.1, 40), graphite), 3.18);
  const collar = add(new THREE.Mesh(cylinder(0.07, 0.1, 0.22, 20), steel), 3.18);
  collar.rotation.z = Math.PI / 2;
  collar.position.x = -side * 0.76;
  const emitter = add(new THREE.Mesh(new THREE.SphereGeometry(0.075, 16, 12), material(PALETTE.softWhite, 0.15, 0.3, PALETTE.warmWhite, 0.8)), 3.18, "coil-emitter");
  emitter.position.x = -side * 0.88;
  setShadows(pillar);
  return pillar;
}

// The near-side console. It stands outside the conveyor lane, square to the
// belt with its back to it, on the factory floor. A cabinet and a dark
// screen, nothing that moves and nothing to catch the eye: the message
// pipes dock on its outward face and that is its whole job in the shot.
function buildConsole(station) {
  const shell = material(0x96938d, 0.55, 0.4);
  const dark = material(0x242322, 0.45, 0.48);
  const steel = material(0xb6b4b1, 0.75, 0.3);
  const terminal = new THREE.Group();
  terminal.name = "orchestration-console";
  terminal.scale.setScalar(0.8);
  // Scaled about its origin, so lift it back onto the floor.
  terminal.position.set(4.2, -2.37 * (1 - terminal.scale.y), 4);
  terminal.rotation.y = Math.PI / 2;
  terminal.updateMatrix();
  station.add(terminal);

  const foot = roundedBox(2.65, 0.3, 2.2, 0.12, dark, 2);
  foot.position.y = -2.22;
  terminal.add(foot);
  const pedestal = roundedBox(1.5, 2, 1.3, 0.15, shell, 2);
  pedestal.position.y = -1.07;
  terminal.add(pedestal);
  // A solid plate closes the gap to the rail in the overhead phone view.
  const mount = roundedBox(0.44, 0.12, 2.1, 0.035, dark, 2);
  mount.name = "console-mount";
  mount.position.set(3.76, 0, terminal.position.z);
  station.add(mount);

  const panel = new THREE.Group();
  panel.position.y = 0.6;
  terminal.add(panel);
  const head = roundedBox(2.75, 1.75, 0.6, 0.16, shell, 2);
  panel.add(head);
  const screen = roundedBox(2.3, 1.3, 0.04, 0.08, material(0x0b100d, 0.05, 0.55), 2);
  screen.name = "console-screen";
  screen.position.z = 0.31;
  panel.add(screen);
  setShadows(terminal);

  return { messageFlows: buildMessagePipes(station, terminal, steel) };
}

// Two glass pipes carry the command in and the acknowledgement out. The
// external controller lives beyond the frame; both lines dock on the outward
// cabinet face, leaving the coil and its activation lane clear. Terminal
// positions derive from the console's transform so moving it cannot
// disconnect them.
function buildMessagePipes(station, terminal, steel) {
  const inlet = new THREE.Vector3(0.4, -0.6, 0.7).applyMatrix4(terminal.matrix);
  const outlet = new THREE.Vector3(-0.25, -0.6, 0.7).applyMatrix4(terminal.matrix);
  const outward = new THREE.Vector3(0, 0, 1).applyQuaternion(terminal.quaternion);
  // Clear walls use alpha rather than a separate transmission render pass.
  const glass = new THREE.MeshStandardMaterial({ color: 0xe3ddd2, metalness: 0.12, roughness: 0.16, transparent: true, opacity: 0.2, depthWrite: false });
  const routes = {
    inbound: [[12, -0.35, 2], [9, -0.35, 3.4], [7.2, -0.45, 4.8], [6.6, inlet.y, 4.7], inlet.clone().addScaledVector(outward, 0.6).toArray(), inlet.toArray()],
    outbound: [outlet.toArray(), outlet.clone().addScaledVector(outward, 0.65).toArray(), [6.6, outlet.y - 0.1, 5.45], [8, -0.85, 5.1], [10, -0.85, 4.4], [13, -0.85, 3.5]],
  };
  const lift = new THREE.Vector3(0, 0.14, 0);
  return Object.entries(routes).map(([direction, points]) => {
    const inbound = direction === "inbound";
    const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)));
    const pipe = new THREE.Group();
    pipe.name = `message-pipe-${direction}`;
    station.add(pipe);
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 56, 0.155, 10, false), glass);
    tube.name = "pipe-glass";
    pipe.add(tube);
    const highlight = new THREE.CatmullRomCurve3(curve.getPoints(48).map((p) => p.add(lift)));
    pipe.add(new THREE.Mesh(new THREE.TubeGeometry(highlight, 48, 0.009, 4, false), steel));
    for (const t of [0, 0.5, 1]) {
      const collar = new THREE.Mesh(torus(0.175, 0.045, 8, 20), steel);
      collar.name = "pipe-collar";
      collar.position.copy(curve.getPointAt(t));
      collar.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), curve.getTangentAt(t));
      pipe.add(collar);
    }
    const socket = new THREE.Mesh(cylinder(0.21, 0.21, 0.2, 20), steel);
    socket.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), outward);
    socket.position.copy(curve.getPoint(inbound ? 1 : 0)).addScaledVector(outward, -0.05);
    pipe.add(socket);
    const capsule = material(inbound ? PALETTE.emberNeon : PALETTE.softWhite, 0.15, 0.35, inbound ? PALETTE.emberDeep : PALETTE.warmWhite, 0.65);
    capsule.transparent = true;
    const packet = new THREE.Mesh(new THREE.CapsuleGeometry(0.105, 0.24, 4, 12), capsule);
    packet.name = "message-capsule";
    packet.visible = false;
    pipe.add(packet);
    return { curve, packet, direction, tangent: new THREE.Vector3() };
  });
}
