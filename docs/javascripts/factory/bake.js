import * as THREE from "../vendor/three.module.js";
import { mergeGeometries } from "../vendor/utils/BufferGeometryUtils.js";

// The models are authored as readable graphs of named parts, and that graph is
// what the geometry probe inspects. Drawn that way the scene was ~490 draw
// calls a frame, each also drawn again into the shadow map. Baking collapses
// every rigid subtree into one mesh per material, which is the same pixels
// for a fraction of the calls.
//
// `bake(root, keep)` rewrites `root` in place: every mesh (instanced ones are
// expanded) that does not move on its own is merged, per material, into one mesh
// parented to `root`. Anything `keep` names stays live, together with its
// descendants, so animated parts keep animating. Hidden meshes merge like any
// other: the caller names every part that changes visibility.
export function bake(root, keep = () => false) {
  root.updateMatrixWorld(true);
  const toRoot = new THREE.Matrix4().copy(root.matrixWorld).invert();
  const byMaterial = new Map();
  const merged = [];
  const doomed = [];
  root.traverse((part) => {
    if (part === root || !isMergeable(part) || keptOrUnder(part, root, keep)) return;
    const key = `${part.material.uuid}:${part.castShadow}:${part.receiveShadow}`;
    if (!byMaterial.has(key)) byMaterial.set(key, { material: part.material, castShadow: part.castShadow, receiveShadow: part.receiveShadow, geometries: [] });
    const matrix = new THREE.Matrix4().multiplyMatrices(toRoot, part.matrixWorld);
    const geometries = byMaterial.get(key).geometries;
    if (part.isInstancedMesh) {
      // A static instanced part (treads, bolts, windings) becomes ordinary
      // geometry here: one merged draw beats one instanced draw per part.
      const instance = new THREE.Matrix4();
      for (let index = 0; index < part.count; index += 1) {
        part.getMatrixAt(index, instance);
        geometries.push(normalized(part.geometry).applyMatrix4(instance.premultiply(matrix)));
      }
    } else {
      geometries.push(normalized(part.geometry).applyMatrix4(matrix));
    }
    doomed.push(part);
  });
  for (const part of doomed) part.removeFromParent();
  pruneEmptyGroups(root);
  for (const { material, castShadow, receiveShadow, geometries } of byMaterial.values()) {
    const mesh = new THREE.Mesh(mergeGeometries(geometries), material);
    for (const geometry of geometries) geometry.dispose();
    mesh.name = `baked:${material.name || material.type}`;
    mesh.castShadow = castShadow;
    mesh.receiveShadow = receiveShadow;
    root.add(mesh);
    merged.push(mesh);
  }
  return merged;
}

function isMergeable(part) {
  return part.isMesh && !part.isSkinnedMesh && !Array.isArray(part.material);
}

// True when the part, or anything between it and the root, is kept.
function keptOrUnder(part, root, keep) {
  for (let node = part; node && node !== root; node = node.parent) if (keep(node)) return true;
  return false;
}

// Merging needs every input to carry the same attribute set and to agree on
// being indexed. Everything this scene builds carries position, normal and
// uv, and is non-indexed after crease normals except the stock primitives,
// so de-index those and drop any other attribute.
function normalized(geometry) {
  // Copy into a plain BufferGeometry: `clone()` constructs the source's own
  // class with default arguments first, which for ExtrudeGeometry extrudes a
  // throwaway default shape (and warns about it) on every call.
  const copy = geometry.index ? geometry.toNonIndexed() : new THREE.BufferGeometry().copy(geometry);
  for (const name of Object.keys(copy.attributes)) {
    if (!["position", "normal", "uv"].includes(name)) copy.deleteAttribute(name);
  }
  if (!copy.attributes.uv) copy.setAttribute("uv", new THREE.Float32BufferAttribute(new Float32Array(copy.attributes.position.count * 2), 2));
  copy.morphAttributes = {};
  copy.clearGroups();
  return copy;
}

function pruneEmptyGroups(root) {
  const empty = [];
  root.traverse((node) => {
    if (node !== root && !node.isMesh && !node.isLine && !node.isLight && node.children.length === 0 && node.type === "Group") empty.push(node);
  });
  for (const node of empty) node.removeFromParent();
  if (empty.length) pruneEmptyGroups(root);
}

// Every robot is built identically, so the first robot's baked geometry is
// shared by all of them. Only the materials differ, because each robot fades
// on its own. Returns the per-role geometry map.
export function shareRobotGeometry(robots) {
  const [template, ...others] = robots;
  const geometryByRole = new Map();
  for (const mesh of template.children) {
    if (mesh.name.startsWith("baked:")) geometryByRole.set(mesh.name, mesh.geometry);
  }
  for (const robot of others) {
    for (const mesh of robot.children) {
      const shared = geometryByRole.get(mesh.name);
      if (shared && shared !== mesh.geometry) {
        mesh.geometry.dispose();
        mesh.geometry = shared;
      }
    }
  }
  return geometryByRole;
}
