import * as THREE from "../vendor/three.module.js";
import { toCreasedNormals } from "../vendor/utils/BufferGeometryUtils.js";

// Geometry primitives shared by every model in the scene. Each is cached by
// its exact arguments: the robots alone ask for the same forty shapes five
// times, and an extruded, re-normalled rounded box is the single most
// expensive thing the scene builds.
const cache = new Map();

function cached(key, build) {
  let geometry = cache.get(key);
  if (!geometry) {
    geometry = build();
    cache.set(key, geometry);
  }
  return geometry;
}

export function material(color, metalness = 0, roughness = 0.5, emissive = 0x000000, emissiveIntensity = 0) {
  return new THREE.MeshStandardMaterial({ color, metalness, roughness, emissive, emissiveIntensity });
}

export function roundedBoxGeometry(width, height, depth, radius, segments = 5) {
  return cached(`box:${width}:${height}:${depth}:${radius}:${segments}`, () => {
    // ExtrudeGeometry adds its bevel OUTSIDE the shape. Budget it inside all
    // three requested extents, especially thin glass in front of a display.
    const bevel = Math.min(radius * 0.33, depth * 0.22);
    const w = width - bevel * 2;
    const h = height - bevel * 2;
    const r = radius - bevel;
    const x = -w / 2;
    const y = -h / 2;
    const shape = new THREE.Shape();
    shape.moveTo(x + r, y);
    shape.lineTo(x + w - r, y);
    shape.quadraticCurveTo(x + w, y, x + w, y + r);
    shape.lineTo(x + w, y + h - r);
    shape.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    shape.lineTo(x + r, y + h);
    shape.quadraticCurveTo(x, y + h, x, y + h - r);
    shape.lineTo(x, y + r);
    shape.quadraticCurveTo(x, y, x + r, y);
    const geometry = new THREE.ExtrudeGeometry(shape, {
      depth: depth - bevel * 2,
      bevelEnabled: true,
      bevelThickness: bevel,
      bevelSize: bevel,
      bevelSegments: segments,
      curveSegments: 12,
    });
    geometry.center();
    return toCreasedNormals(geometry);
  });
}

export function roundedBox(width, height, depth, radius, surface, segments = 5) {
  return new THREE.Mesh(roundedBoxGeometry(width, height, depth, radius, segments), surface);
}

export function cylinder(top, bottom, height, radialSegments) {
  return cached(`cyl:${top}:${bottom}:${height}:${radialSegments}`, () => new THREE.CylinderGeometry(top, bottom, height, radialSegments));
}

export function torus(radius, tube, radialSegments, tubularSegments) {
  return cached(`torus:${radius}:${tube}:${radialSegments}:${tubularSegments}`, () => new THREE.TorusGeometry(radius, tube, radialSegments, tubularSegments));
}

export function circle(radius, segments) {
  return cached(`circle:${radius}:${segments}`, () => new THREE.CircleGeometry(radius, segments));
}

export function box(width, height, depth) {
  return cached(`cube:${width}:${height}:${depth}`, () => new THREE.BoxGeometry(width, height, depth));
}

// A stack of coaxial discs: antenna segments, ear caps, wheel hubs, coil
// columns. Each row is [radius, height, offset along the axis, material].
export function discStack(rows, radialSegments, axis = "y") {
  const group = new THREE.Group();
  for (const [radius, height, offset, surface] of rows) {
    const disc = new THREE.Mesh(cylinder(radius, radius, height, radialSegments), surface);
    if (axis === "x") disc.rotation.z = Math.PI / 2;
    disc.position[axis] = offset;
    group.add(disc);
  }
  return group;
}

// Screw heads with a slot: two instanced draws for any number of them.
export function fasteners(placements, metal, inset) {
  const group = new THREE.Group();
  group.name = "fasteners";
  const heads = new THREE.InstancedMesh(
    cached("fastener-head", () => new THREE.CylinderGeometry(0.047, 0.047, 0.025, 16).rotateX(Math.PI / 2)),
    metal,
    placements.length,
  );
  const slots = new THREE.InstancedMesh(box(0.052, 0.012, 0.004), inset, placements.length);
  const pose = new THREE.Object3D();
  placements.forEach(({ position, rotation }, index) => {
    pose.position.fromArray(position);
    pose.rotation.set(...rotation);
    pose.updateMatrix();
    heads.setMatrixAt(index, pose.matrix);
    pose.translateZ(0.015);
    pose.updateMatrix();
    slots.setMatrixAt(index, pose.matrix);
  });
  group.add(heads, slots);
  return group;
}

// Lay out `count` instances of one geometry by a pose callback.
export function instanced(geometry, surface, count, place) {
  const mesh = new THREE.InstancedMesh(geometry, surface, count);
  const pose = new THREE.Object3D();
  for (let index = 0; index < count; index += 1) {
    pose.position.set(0, 0, 0);
    pose.rotation.set(0, 0, 0);
    place(pose, index);
    pose.updateMatrix();
    mesh.setMatrixAt(index, pose.matrix);
  }
  return mesh;
}

export function setShadows(root, cast = true, receive = true, except = null) {
  root.traverse((part) => {
    if (part.isMesh && part !== except) {
      part.castShadow = cast;
      part.receiveShadow = receive;
    }
  });
}
