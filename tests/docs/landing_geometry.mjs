import assert from 'node:assert/strict';
import * as THREE from '../../docs/javascripts/vendor/three.module.js';

// Only the browser entrypoint is stubbed; all scene construction uses Three.js.
globalThis.window = { matchMedia: () => ({ matches: false }) };
globalThis.document = { querySelector: () => null };
const { FactoryScene, TRAVEL } = await import('../../docs/javascripts/grove-factory.js');
const factory = Object.create(FactoryScene.prototype);
factory.scene = new THREE.Scene();
const thinPanel = factory.roundedBox(1.8, 1.7, 0.075, 0.34, new THREE.MeshStandardMaterial());
const panelSize = new THREE.Box3().setFromObject(thinPanel).getSize(new THREE.Vector3());
for (const [axis, expected] of [['x', 1.8], ['y', 1.7], ['z', 0.075]]) {
  assert.ok(Math.abs(panelSize[axis] - expected) < 0.001, `bevel must stay inside requested ${axis} extent`);
}
const spec = {};
const robot = factory.makeRobot(spec);
robot.updateMatrixWorld(true);

for (const name of ['screen-halo', 'shell-seam', 'service-panel', 'ear', 'fasteners']) {
  assert.ok(robot.getObjectByName(name), `missing physical robot detail: ${name}`);
}
const tracks = [];
robot.traverse(object => { if (object.name === 'track') tracks.push(object); });
assert.equal(tracks.length, 2, 'the robot must have two real rubber tracks');
for (const track of tracks) {
  assert.ok(track.getObjectByName('treads').isInstancedMesh, 'repeated tread geometry must be batched');
  assert.ok(track.getObjectByName('hub'), 'track needs a separate wheel hub');
}
const bounds = new THREE.Box3().setFromObject(robot);
assert.ok(bounds.min.y < -1.05, 'undercarriage must extend below the shell');
assert.ok(bounds.max.x < 2, 'robot must fit between the conveyor rails');
assert.ok(spec.display.isMesh);
assert.equal(spec.display.visible, false, 'never flash an untextured display');

factory.addBelt();
const slats = factory.belt.getObjectByName('belt-slats');
assert.ok(slats?.isInstancedMesh, 'belt slats must have physical depth without a draw call per slat');
assert.ok(slats.count >= 30);
assert.ok(factory.belt.getObjectByName('belt-roller'));
assert.ok(factory.belt.getObjectByName('belt-support'));
const first = new THREE.Matrix4();
slats.getMatrixAt(0, first);
factory.elapsed = 2;
factory.robotRunners = Array.from({ length: 5 }, (_, index) => ({ phase: index / 5 }));
factory.updateBelt();
const moved = new THREE.Matrix4();
slats.getMatrixAt(0, moved);
assert.notDeepEqual(first.elements, moved.elements, 'the physical belt must move with its riders');
factory.elapsed = 0;
factory.updateBelt();
slats.getMatrixAt(0, moved);
assert.deepEqual(first.elements, moved.elements, 'reduced-motion frame must be deterministic');
// Rasterizing/loading images are browser I/O. The real placement, scanner, and
// status-transition paths run over actual geometry and texture objects.
factory.makeCheckTexture = () => new THREE.Texture();
factory.applyRobotBadge = runner => { runner.logoTexture = new THREE.Texture(); };
factory.addScanner();
factory.addRobots();
factory.addAtmosphere();
const floorGrid = factory.scene.getObjectByName('factory-floor-grid');
assert.ok(floorGrid.isLineSegments && floorGrid.material.opacity <= 0.2);
assert.ok(floorGrid.position.y < factory.belt.position.y, 'the grid stays under the hardware');
const station = factory.belt.getObjectByName('station');
const orchestration = station.getObjectByName('orchestration-console');
assert.ok(orchestration, 'the scanner station needs its outside orchestration console');
station.updateWorldMatrix(true, true);
const consoleBounds = new THREE.Box3().setFromObject(orchestration);
assert.ok(Math.abs(consoleBounds.min.y + 3.12) < 0.01, 'console must be grounded on the factory floor');
assert.ok(orchestration.position.x > 3.6 && orchestration.position.z > 2, 'console sits outside the right rail, clear of the coil');
for (const name of ['console-screen', 'console-sliders', 'console-dials', 'console-fasteners']) assert.ok(orchestration.getObjectByName(name), `missing ${name}`);
assert.ok(orchestration.getObjectByName('console-keys')?.isInstancedMesh, 'console keys must be instanced');
for (const name of ['message-pipe-inbound', 'message-pipe-outbound']) {
  const pipe = station.getObjectByName(name);
  assert.ok(pipe, `missing ${name}`);
  assert.ok(pipe.getObjectByName('pipe-glass')?.material.transparent, `${name} needs a transparent glass run`);
  const collars = [];
  pipe.traverse(part => { if (part.name === 'pipe-collar') collars.push(part); });
  assert.ok(collars.length >= 2 && collars.every(part => part.material.metalness > 0), `${name} needs metal collar endpoints`);
}
const flows = factory.messageFlows;
assert.deepEqual(flows.map(flow => flow.direction).sort(), ['inbound', 'outbound']);
for (const flow of flows) assert.ok(flow.curve.isCatmullRomCurve3 && flow.packet.isMesh, `${flow.direction} flow needs a curve and packet mesh`);
const consoleOutward = new THREE.Vector3(0, 0, 1).applyQuaternion(orchestration.quaternion);
assert.ok(consoleOutward.x > 0.999 && Math.abs(consoleOutward.z) < 0.001, 'console is square to the belt at exactly ninety degrees');
assert.ok(orchestration.getObjectByName('console-screen').parent.rotation.x === 0, 'console panel is upright, not tilted');
assert.ok(station.getObjectByName('console-mount'), 'a solid mounting plate closes the overhead gap to the rail');
for (const flow of flows) {
  const outside = flow.curve.getPoint(flow.direction === 'inbound' ? 0 : 1);
  const port = flow.curve.getPoint(flow.direction === 'inbound' ? 1 : 0);
  const localPort = port.clone().applyMatrix4(orchestration.matrix.clone().invert());
  assert.ok(outside.x > 11, 'messages originate or terminate beyond the right viewport, not at a coil');
  assert.ok(Math.abs(localPort.z - 0.7) < 0.001 && Math.abs(localPort.x) < 0.7 && localPort.y < 0, 'both pipes dock on the console cabinet outward face');
  assert.ok(flow.curve.getPoints(40).every(point => point.sub(orchestration.position).dot(consoleOutward) > 0.5), 'pipes stay outward of the console, never between it and the coil');
}
const seen = { inbound: false, outbound: false, faded: { inbound: false, outbound: false } };
for (let elapsed = 0; elapsed <= 20; elapsed += 0.25) {
  factory.elapsed = elapsed;
  factory.updateRobots();
  for (const flow of flows) {
    const range = flow.direction === 'inbound' ? [-3, -0.6] : [1.2, 3.6];
    const riders = factory.robotRunners.filter(runner => runner.delayedTravel >= range[0] && runner.delayedTravel <= range[1]);
    assert.ok(riders.length <= 1, `${flow.direction} accepts at most one rider`);
    assert.equal(flow.packet.visible, riders.length === 1, `${flow.direction} packet is hidden outside its interval`);
    if (!flow.packet.visible) continue;
    const progress = flow.packet.userData.progress;
    assert.ok(progress >= 0 && progress <= 1 && flow.packet.position.distanceTo(flow.curve.getPointAt(progress)) < 0.0001, `${flow.direction} packet follows its station-space curve`);
    assert.ok(new THREE.Vector3(0, 1, 0).applyQuaternion(flow.packet.quaternion).dot(flow.curve.getTangentAt(progress)) > 0.999, 'packet local Y follows the pipe tangent');
    seen[flow.direction] = true;
    seen.faded[flow.direction] ||= (progress < 0.2 || progress > 0.8) && flow.packet.material.opacity < 0.99;
    if (flow.direction === 'inbound') assert.ok(factory.activationEnvelope(riders[0].delayedTravel).charge > 0, 'inbound packets only move while the right coil charges');
    else assert.ok(factory.robotRunners.every(runner => !runner.arcs[1].visible), 'outbound packets wait for the right arc to release');
  }
}
assert.ok(seen.inbound && seen.outbound && seen.faded.inbound && seen.faded.outbound, 'both packets traverse and fade at their pipe terminals');
factory.elapsed = 8;
factory.updateRobots();
const flowState = flows.map(flow => ({ curve: flow.curve, geometry: flow.packet.geometry, position: flow.packet.position.clone(), progress: flow.packet.userData.progress }));
factory.updateRobots();
flows.forEach((flow, index) => {
  assert.equal(flow.curve, flowState[index].curve, 'animation retains pipe geometry');
  assert.equal(flow.packet.geometry, flowState[index].geometry, 'animation retains packet geometry');
  assert.ok(flow.packet.position.distanceTo(flowState[index].position) < 1e-10 && flow.packet.userData.progress === flowState[index].progress, 'the same elapsed time is stable');
});
const pillars = station.children.filter(part => part.name === 'activation-pillar');
assert.equal(pillars.length, 2, 'activation needs two independent coil pillars');
assert.ok(pillars[0].position.x < 0 && pillars[1].position.x > 0, 'pillars flank the belt');
assert.ok(!station.getObjectByName('portal-arch'), 'no overhead gate remains');
assert.ok(!station.getObjectByName('station-arm'), 'the old arm must not clutter the coils');
station.updateWorldMatrix(true, true);
for (const pillar of pillars) {
  const footprint = new THREE.Box3().setFromObject(pillar);
  assert.ok(Math.abs(footprint.min.y + 3.12) < 0.01, 'each pillar is grounded on the factory floor');
  assert.ok(pillar.getObjectByName('coil-windings')?.isInstancedMesh);
  assert.ok(pillar.getObjectByName('coil-crown'));
  assert.ok(pillar.getObjectByName('coil-emitter'));
}
// A physical ray along the robot's antenna height must pass between the pillars.
const passage = new THREE.Raycaster(
  station.localToWorld(new THREE.Vector3(0, 3.4, -4)),
  new THREE.Vector3(0, 0, 1).transformDirection(station.matrixWorld),
  0, 8,
);
assert.equal(passage.intersectObjects(pillars, true).length, 0, 'the activation lane stays open');
const crossing = factory.robotRunners[0];
factory.elapsed = 1;
factory.updateRobots();
assert.equal(crossing.displayMaterial.map, crossing.logoTexture, 'wait for the delayed coil before verification');
factory.elapsed = 3;
factory.updateRobots();
assert.equal(crossing.displayMaterial.map, factory.checkTexture, 'a cleared robot with both coils finished must be verified');
// The frame loop calls updateRobots, never updateBelt, so pin the belt there.
factory.elapsed = 0;
factory.updateRobots();
const restingSlat = new THREE.Matrix4();
slats.getMatrixAt(0, restingSlat);
factory.elapsed = 2;
factory.updateRobots();
const travelledSlat = new THREE.Matrix4();
slats.getMatrixAt(0, travelledSlat);
assert.ok(!restingSlat.equals(travelledSlat), 'each animation frame must advance the belt with the robots');
for (const elapsed of [0, 2, 19.9, 20.1]) {
  factory.elapsed = elapsed;
  factory.updateRobots();
  factory.belt.updateMatrixWorld(true);
  for (const runner of factory.robotRunners) {
    const lowest = new THREE.Box3().setFromObject(runner.group).min.y;
    assert.ok(Math.abs(lowest - (-1.1 + 0.41)) < 0.001, 'tracks must stay seated on the moving deck');
    const inBeltSpace = new THREE.Box3().setFromObject(runner.group.clone());
    const delayedBounds = inBeltSpace.clone().translate(new THREE.Vector3(0, 0, runner.delayedTravel - runner.group.position.z));
    const expected = inBeltSpace.min.z >= 0 && delayedBounds.min.z >= 0 ? factory.checkTexture : runner.logoTexture;
    assert.equal(runner.displayMaterial.map, expected, 'scanner transition must survive the visual redesign');
    const ray = new THREE.Raycaster(new THREE.Vector3(0, 0.24, 4), new THREE.Vector3(0, 0, -1));
    // Transform the local face ray into the moving robot's world coordinates.
    ray.ray.applyMatrix4(runner.group.matrixWorld);
    const hits = ray.intersectObject(runner.group, true);
    // Compare identity as a boolean: a failing deep diff of two Mesh graphs never returns.
    assert.ok(hits[0]?.object === runner.display, `the screen cover must not hide its glyph, first hit ${hits[0]?.object.name || hits[0]?.object.type}`);
  }
}
assert.ok(factory.robotRunners[0].group.scale.x < 1.12, 'keep the smaller robot scale');
const arcs = station.getObjectByName('activation-arcs');
assert.equal(arcs.children.length, factory.robotRunners.length * 2);
factory.elapsed = 0.6;
factory.updateRobots();
const nearest = factory.robotRunners.reduce((best, runner) => (Math.abs(runner.group.position.z) < Math.abs(best.group.position.z) ? runner : best));
const farthest = factory.robotRunners.reduce((best, runner) => (Math.abs(runner.group.position.z) > Math.abs(best.group.position.z) ? runner : best));
assert.ok(nearest.arcs.every(arc => arc.visible), 'the centered robot receives paired activation links');
assert.ok(farthest.arcs.every(arc => !arc.visible), 'distant robots receive no arcs');
for (const arc of nearest.arcs) {
  const core = arc.getObjectByName('arc-core');
  assert.ok(core.isInstancedMesh, 'reuse GPU geometry rather than rebuilding tubes each frame');
  const matrix = new THREE.Matrix4();
  core.getMatrixAt(0, matrix);
  const start = new THREE.Vector3(0, -0.5, 0).applyMatrix4(matrix);
  core.getMatrixAt(core.count - 1, matrix);
  const end = new THREE.Vector3(0, 0.5, 0).applyMatrix4(matrix);
  const source = arcs.worldToLocal(arc.userData.emitter.getWorldPosition(new THREE.Vector3()));
  const target = arcs.worldToLocal(arc.userData.antenna.getWorldPosition(new THREE.Vector3()));
  assert.ok(source.x * target.x > 0, 'each pillar activates the antenna on its own side');
  assert.ok(start.distanceTo(source) < 0.001, 'the rendered arc must begin at a real coil terminal');
  assert.ok(end.distanceTo(target) < 0.001, 'the rendered arc must terminate on its antenna');
  const geometry = core.geometry;
  factory.updateArc(arc, 0.6, 0.2);
  assert.ok(core.geometry === geometry, 'animation must not allocate replacement geometry');
}
const charging = factory.activationEnvelope(-2);
const connected = factory.activationEnvelope(0);
const released = factory.activationEnvelope(3);
assert.ok(charging.charge > 0 && charging.link === 0, 'coils charge before making contact');
assert.ok(connected.link > 0.99, 'links peak at the center of the station');
assert.ok(released.charge === 0 && released.link === 0, 'coils settle after release');
const starts = [null, null];
for (let time = 18; time <= 23; time += 0.02) {
  factory.elapsed = time;
  factory.updateRobots();
  for (const runner of factory.robotRunners) {
    if (runner.arcs.some(arc => arc.visible)) {
      assert.ok(runner.displayMaterial.map !== factory.checkTexture, 'a robot must not be marked verified while either coil is still connected');
    }
  }
  factory.robotRunners[0].arcs.forEach((arc, side) => {
    if (starts[side] === null && arc.visible && arc.getObjectByName('arc-core').material.opacity > 0.08) starts[side] = time;
  });
}
assert.ok(starts.every(time => time !== null), 'observe both coils activating in one complete pass');
assert.ok(starts[1] - starts[0] >= 1, `coil activation must be separated by at least one second, got ${starts[1] - starts[0]}`);
for (const edge of [-1.2, 1.2]) {
  const before = factory.activationEnvelope(edge - 0.001).link;
  const after = factory.activationEnvelope(edge + 0.001).link;
  assert.ok(Math.abs(after - before) < 0.01, 'activation fades instead of flashing on or off');
}
// Robots fade in from the dark end of the belt and out before the loop wraps,
// so a rider never pops into or out of existence. Opacity is a smooth
// function of position, fully opaque through the middle of the run.
const opacityOf = (runner) => runner.bodyMaterial.opacity;
const spawnEnd = -TRAVEL;
const probe = factory.robotRunners[0];
const samples = [];
for (const z of [spawnEnd, spawnEnd + 1, spawnEnd + 3, 0, -spawnEnd - 3, -spawnEnd - 1, -spawnEnd]) {
  probe.group.position.z = z;
  factory.updateFade(probe, z);
  samples.push([z, opacityOf(probe)]);
}
assert.ok(samples[0][1] < 0.05, `a robot at the spawn edge must be invisible, got ${samples[0][1]}`);
assert.ok(samples[1][1] > samples[0][1] && samples[2][1] > samples[1][1], 'opacity must rise smoothly after spawn');
assert.ok(samples[3][1] > 0.99, 'a robot mid-run must be fully opaque');
assert.ok(samples[5][1] < samples[4][1] && samples[6][1] < samples[5][1], 'opacity must fall smoothly before the wrap');
assert.ok(samples[6][1] < 0.05, `a robot at the wrap edge must be invisible, got ${samples[6][1]}`);
assert.ok(probe.bodyMaterial.transparent, 'shell materials must be transparent to fade');
assert.equal(probe.displayMaterial.opacity, opacityOf(probe), 'the face fades with the shell');
// Transparency is only for the fade. A solid robot drawn transparent loses
// early depth rejection and is re-sorted every frame for nothing.
probe.group.position.z = 0;
factory.updateFade(probe, 0);
assert.ok(probe.fadeMaterials.filter(m => m !== probe.displayMaterial).every(m => !m.transparent), 'a fully opaque robot must not render as transparent');
factory.updateFade(probe, spawnEnd + 1);
assert.ok(probe.bodyMaterial.transparent, 'a fading robot becomes transparent again');

// Baking collapses the authored graph into per-material meshes. It must draw
// the same triangles in the same place, keep every animated part live, and
// leave the choreography working on the baked scene.
// Bounds over every world-space vertex, instances expanded. Box3's own
// instanced path boxes each instance's local AABB, which over-states a rotated
// part, so it cannot compare an instanced tread with the geometry it bakes into.
const vertexBounds = (root) => {
  const box = new THREE.Box3();
  const point = new THREE.Vector3();
  const instance = new THREE.Matrix4();
  root.updateMatrixWorld(true);
  root.traverse(o => {
    if (!o.isMesh || !o.visible) return;
    const position = o.geometry.attributes.position;
    const count = o.isInstancedMesh ? o.count : 1;
    for (let i = 0; i < count; i += 1) {
      if (o.isInstancedMesh) o.getMatrixAt(i, instance); else instance.identity();
      instance.premultiply(o.matrixWorld);
      for (let v = 0; v < position.count; v += 1) box.expandByPoint(point.fromBufferAttribute(position, v).applyMatrix4(instance));
    }
  });
  return box;
};
const tally = () => {
  let calls = 0, triangles = 0, casters = 0;
  factory.scene.updateMatrixWorld(true);
  factory.scene.traverse(o => {
    if (!(o.isMesh || o.isLine)) return;
    calls += 1;
    const g = o.geometry;
    triangles += ((g.index ? g.index.count : g.attributes.position.count) / 3) * (o.isInstancedMesh ? o.count : 1);
    if (o.castShadow) casters += 1;
  });
  return { calls, triangles: Math.round(triangles), casters, bounds: vertexBounds(factory.belt), robots: factory.robotRunners.map(runner => vertexBounds(runner.group)) };
};
factory.elapsed = 0.6;
factory.updateRobots();
const authored = tally();
factory.bake();
factory.updateRobots();
const baked = tally();
assert.equal(baked.triangles, authored.triangles, 'baking must not add or drop geometry');
const same = (a, b) => a.min.distanceTo(b.min) < 1e-3 && a.max.distanceTo(b.max) < 1e-3;
assert.ok(same(baked.bounds, authored.bounds), 'baked geometry stays where it was authored');
assert.ok(baked.robots.every((box, index) => same(box, authored.robots[index])), 'every baked robot keeps its authored shape');
assert.ok(baked.calls * 4 < authored.calls, `baking must collapse draw calls, ${authored.calls} -> ${baked.calls}`);
assert.ok(baked.casters * 4 < authored.casters, `baking must collapse shadow casters, ${authored.casters} -> ${baked.casters}`);
const [firstRobot, ...otherRobots] = factory.robotRunners.map(runner => runner.group);
const shell = firstRobot.getObjectByName('baked:robot-body');
assert.ok(shell, 'each robot bakes to per-role meshes');
assert.ok(otherRobots.every(robot => robot.getObjectByName('baked:robot-body').geometry === shell.geometry), 'robots share one set of baked geometry');
assert.ok(otherRobots.every(robot => robot.getObjectByName('baked:robot-body').material !== shell.material), 'each robot keeps its own materials so it fades alone');
for (const runner of factory.robotRunners) {
  assert.ok(runner.display.parent && runner.antennaTips.every(tip => tip.parent), 'the display and antenna tips survive baking');
}
assert.ok(factory.beltSlats.parent && factory.beltSlats.isInstancedMesh, 'the moving slats stay live');
assert.ok(factory.coilEmitters.every(emitter => emitter.parent), 'the coil emitters stay live');
// Each indicator band glows to its own charge level, so each must still be
// drawn by its own material rather than folded into a shared merge.
for (const { material } of factory.coilIndicators) {
  const users = [];
  factory.scene.traverse(o => { if (o.isMesh && o.material === material) users.push(o); });
  assert.equal(users.length, 1, 'each coil band keeps its own live mesh');
  assert.ok(!users[0].name.startsWith('baked:'), 'a coil band is never merged');
}
assert.equal(station.getObjectByName('activation-arcs').children.length, factory.robotRunners.length * 2, 'hidden arcs must not vanish into the bake');
for (const flow of flows) assert.ok(flow.packet.parent, 'message packets stay live');
const linked = factory.robotRunners.find(runner => runner.arcs.some(arc => arc.visible));
assert.ok(linked, 'arcs still fire after baking');
for (const arc of linked.arcs.filter(a => a.visible)) {
  const core = arc.getObjectByName('arc-core');
  const matrix = new THREE.Matrix4();
  core.getMatrixAt(core.count - 1, matrix);
  const end = new THREE.Vector3(0, 0.5, 0).applyMatrix4(matrix);
  const target = arcs.worldToLocal(arc.userData.antenna.getWorldPosition(new THREE.Vector3()));
  assert.ok(end.distanceTo(target) < 0.001, 'a baked robot\'s arc still lands on its antenna');
}

// The frame governor caps the rate and trades quality for frame rate: MSAA
// first (half the frame under a software rasterizer), then pixel ratio.
const { FrameGovernor } = await import('../../docs/javascripts/factory/frame-governor.js');
// Feeds frames every `interval` ms from `start` until a quality change or `until`.
const run = (governor, start, until, interval) => {
  for (let time = start; time < until; time += interval) {
    const changed = governor.record(time);
    if (changed) return { changed, time };
  }
  return { changed: null, time: until };
};
const governor = new FrameGovernor({ maxFps: 60, ceiling: 1.5, floor: 0.6, samples: 2 });
assert.deepEqual(governor.quality, { pixelRatio: 1.5, samples: 2 }, 'a hardware renderer starts at full quality');
assert.ok(governor.shouldRender(0), 'the first refresh renders');
governor.record(0);
assert.ok(!governor.shouldRender(7), 'a 144 Hz refresh between frames is skipped');
assert.ok(governor.shouldRender(16), 'a 60 Hz refresh renders');
// Two frames a second: the rescue is measured in wall time, not frame counts.
let step = run(governor, 500, 10_000, 500);
assert.ok(step.time <= 1500, `a 2 fps scene must drop quality within about a second, took ${step.time} ms`);
assert.deepEqual(step.changed, { pixelRatio: 1.5, samples: 0 }, 'multisampling is the first thing dropped');
step = run(governor, step.time + 500, 20_000, 500);
assert.deepEqual(step.changed, { pixelRatio: 1.35, samples: 0 }, 'then the pixel ratio steps down');
for (let time = step.time + 500; time < 60_000; time += 500) governor.record(time);
assert.deepEqual(governor.quality, { pixelRatio: 0.6, samples: 0 }, 'sustained slow frames settle at the floor');
// A step larger than the headroom must clamp, not overshoot below the floor.
const coarse = new FrameGovernor({ maxFps: 60, ceiling: 1.5, floor: 0.6, step: 1, samples: 0 });
assert.deepEqual(run(coarse, 0, 5000, 50).changed, { pixelRatio: 0.6, samples: 0 }, 'one coarse step clamps to the floor');
// Headroom earns quality back, one rung at a time.
step = run(governor, 100_000, 120_000, 16);
assert.deepEqual(step.changed, { pixelRatio: 0.75, samples: 0 }, 'sustained on-time frames restore resolution');
// A climb that proves too slow is undone, and the next attempt waits longer.
const undone = run(governor, step.time + 50, step.time + 5000, 50);
assert.deepEqual(undone.changed, { pixelRatio: 0.6, samples: 0 }, 'a climb that cannot hold frame rate is undone');
const retry = run(governor, undone.time + 16, undone.time + 60_000, 16);
assert.ok(retry.time - undone.time >= 8000, 'after a failed climb the governor waits twice as long to retry');
// A software rasterizer starts at the bottom and must earn quality.
const software = new FrameGovernor({ maxFps: 60, ceiling: 1.5, floor: 0.6, samples: 2, software: true });
assert.deepEqual(software.quality, { pixelRatio: 0.6, samples: 0 }, 'a software renderer starts at the floor without MSAA');
assert.equal(run(software, 0, 20_000, 500).changed, null, 'a slow software renderer stays at the floor');
// The resolution range is a pixel budget: a phone's small band renders at its
// native density, while the desktop hero keeps its measured range.
const desktop = FrameGovernor.range({ devicePixelRatio: 2, cssPixels: 992 * 900 });
assert.deepEqual(desktop, { ceiling: 1.5, floor: 0.6, step: 0.15 }, 'the desktop hero keeps its range');
const phone = FrameGovernor.range({ devicePixelRatio: 3, cssPixels: 390 * 219 });
assert.equal(phone.ceiling, 3, 'a 3x phone renders its small band at native density, double the old cap');
assert.ok(phone.floor >= 1.5, 'a phone shedding quality never drops below the old cap');
assert.equal(FrameGovernor.range({ devicePixelRatio: 2.625, cssPixels: 412 * 232 }).ceiling, 2.63, 'a 2.6x phone gets its native density');
assert.equal(FrameGovernor.range({ devicePixelRatio: 4, cssPixels: 390 * 219 }).ceiling, 3, 'density is capped at 3');
assert.equal(FrameGovernor.range({ devicePixelRatio: 1, cssPixels: 390 * 219 }).ceiling, 1, 'never above the display density');
assert.equal(FrameGovernor.range({ devicePixelRatio: 3, cssPixels: 1024 * 1366 }).ceiling, 1.5, 'a large canvas never drops below the old cap');
const ladder = FrameGovernor.ladder({ ...phone, samples: 2 });
assert.equal(ladder.at(-1).pixelRatio, phone.floor, 'the phone ladder ends at its floor');
assert.ok(ladder.length <= 9, 'a phone sheds quality in as many steps as the desktop');
let scheduled = 0;
globalThis.requestAnimationFrame = () => { scheduled += 1; return scheduled; };
globalThis.cancelAnimationFrame = () => {};
factory.started = true;
factory.frame = null;
factory.paused = true;
factory.visible = true;
factory.schedule();
assert.equal(scheduled, 0, 'reduced motion must not schedule animation');
factory.paused = false;
factory.visible = false;
factory.schedule();
assert.equal(scheduled, 0, 'offscreen scenes must not schedule animation');
factory.visible = true;
document.hidden = true;
factory.schedule();
assert.equal(scheduled, 0, 'hidden documents must not schedule animation');
document.hidden = false;
factory.schedule();
assert.equal(scheduled, 1, 'a visible moving scene must schedule a frame');
console.log('Robot geometry, conveyor geometry, synchronized motion, screen visibility, and lifecycle passed');
