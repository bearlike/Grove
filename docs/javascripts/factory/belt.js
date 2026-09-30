import * as THREE from "../vendor/three.module.js";
import { BELT_LENGTH, BELT_WIDTH, PALETTE } from "./palette.js";
import { box, cylinder, fasteners, material, roundedBox, roundedBoxGeometry } from "./parts.js";

const SLAT_COUNT = 42;
const SLAT_PITCH = BELT_LENGTH / SLAT_COUNT;

// The conveyor: a frame, two chrome rails with a neon strip each (ember on the
// near side, the scene's one accent), legs and rollers, and a deck of slats
// that moves with the riders. The returned group is the belt's coordinate
// space, which every other model on the line is placed in.
export function buildBelt() {
  const belt = new THREE.Group();
  belt.position.set(0.3, -1.1, 0);
  belt.rotation.y = 0.2;

  const steel = material(0x353432, 0.55, 0.48);
  const frame = material(0x242322, 0.65, 0.42);
  const rail = material(0xb6b4b1, 0.8, 0.3);
  const surface = roundedBox(BELT_WIDTH, 0.68, BELT_LENGTH, 0.12, frame);
  surface.position.y = -0.11;
  surface.receiveShadow = true;
  belt.add(surface);

  const slats = new THREE.InstancedMesh(roundedBoxGeometry(BELT_WIDTH - 0.14, 0.18, SLAT_PITCH - 0.04, 0.055, 2), steel, SLAT_COUNT);
  slats.name = "belt-slats";
  slats.castShadow = true;
  slats.receiveShadow = true;
  slats.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  // All slats stay inside this envelope, including the overhang at a wrap.
  // A fixed bound avoids re-transforming every instance just for culling.
  slats.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0.32, 0), Math.hypot(BELT_WIDTH / 2, BELT_LENGTH / 2 + SLAT_PITCH));
  belt.add(slats);
  moveSlats(slats, 0);

  const bolts = [];
  for (const side of [-1, 1]) {
    const edge = roundedBox(0.27, 0.66, BELT_LENGTH + 0.3, 0.09, rail);
    edge.position.set(side * (BELT_WIDTH / 2 + 0.08), 0.08, 0);
    edge.castShadow = true;
    belt.add(edge);
    const channel = new THREE.Mesh(box(0.06, 0.14, BELT_LENGTH), frame);
    channel.position.set(side * (BELT_WIDTH / 2 + 0.23), -0.04, 0);
    belt.add(channel);
    const near = side > 0;
    const neon = new THREE.Mesh(
      box(0.04, 0.045, BELT_LENGTH),
      material(near ? PALETTE.emberNeon : PALETTE.softWhite, 0, 0.4, near ? PALETTE.emberDeep : PALETTE.warmWhite, near ? 0.9 : 0.35),
    );
    neon.position.set(side * (BELT_WIDTH / 2 + 0.265), -0.04, 0);
    belt.add(neon);
    for (let z = -15; z <= 15; z += 3) bolts.push({ position: [side * (BELT_WIDTH / 2 + 0.24), 0.25, z], rotation: [0, side * Math.PI / 2, 0] });
    for (const z of [-12, -4, 4, 12]) {
      const support = roundedBox(0.45, 1.5, 0.65, 0.08, frame);
      support.name = "belt-support";
      support.position.set(side * 2.6, -1.12, z);
      support.castShadow = true;
      belt.add(support);
      const foot = roundedBox(0.9, 0.16, 1.1, 0.07, rail);
      foot.position.set(side * 2.6, -1.94, z);
      belt.add(foot);
    }
  }
  belt.add(fasteners(bolts, rail, frame));
  for (const z of [-BELT_LENGTH / 2, BELT_LENGTH / 2]) {
    const roller = new THREE.Mesh(cylinder(0.45, 0.45, BELT_WIDTH, 32), frame);
    roller.name = "belt-roller";
    roller.rotation.z = Math.PI / 2;
    roller.position.set(0, -0.08, z);
    belt.add(roller);
    for (const side of [-1, 1]) {
      const cap = new THREE.Mesh(cylinder(0.24, 0.24, 0.14, 24), rail);
      cap.rotation.z = Math.PI / 2;
      cap.position.set(side * (BELT_WIDTH / 2 + 0.1), -0.08, z);
      belt.add(cap);
    }
  }
  belt.add(buildServiceIsland());
  return belt;
}

const slatPose = new THREE.Object3D();

// Slide every slat `distance` along the belt, wrapping at the rollers.
export function moveSlats(slats, distance) {
  for (let index = 0; index < slats.count; index += 1) {
    slatPose.position.set(0, 0.32, (index * SLAT_PITCH + distance) % BELT_LENGTH - BELT_LENGTH / 2 + SLAT_PITCH / 2);
    slatPose.updateMatrix();
    slats.setMatrixAt(index, slatPose.matrix);
  }
  slats.instanceMatrix.needsUpdate = true;
}

// The far-side service island repeats the belt's axis. The near-side floor is
// reserved for the orchestration console and its grounded base.
function buildServiceIsland() {
  const island = new THREE.Group();
  island.name = "service-island";
  const stone = material(0x33312f, 0.22, 0.65);
  const inset = material(0x1b1a19, 0.35, 0.5);
  const [x, z] = [-5.7, -7];
  const slab = roundedBox(2.6, 0.48, 6.8, 0.2, stone);
  slab.position.set(x, -1.78, z);
  slab.castShadow = true;
  slab.receiveShadow = true;
  island.add(slab);
  const tray = roundedBox(2.25, 0.035, 5.9, 0.14, inset);
  tray.position.set(x, -1.53, z);
  tray.receiveShadow = true;
  island.add(tray);
  for (const dz of [-1.6, 0, 1.6]) {
    const joint = new THREE.Mesh(box(2.1, 0.008, 0.018), stone);
    joint.position.set(x, -1.51, z + dz);
    island.add(joint);
  }
  return island;
}

// The page's floor and a sparse grid, both under the hardware so machines
// occlude the lines rather than wearing them.
export function buildFloor(beltRotation) {
  const floorMaterial = material(PALETTE.night, 0, 1);
  // Studio reflections belong on the hardware, not in the page's black floor.
  floorMaterial.envMapIntensity = 0.08;
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(120, 120), floorMaterial);
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = -3.12;
  floor.receiveShadow = true;
  const grid = new THREE.GridHelper(80, 40, 0x35312b, 0x242220);
  grid.name = "factory-floor-grid";
  grid.position.y = -3.1;
  grid.rotation.y = beltRotation;
  grid.material.transparent = true;
  grid.material.opacity = 0.2;
  grid.material.depthWrite = false;
  return [floor, grid];
}
