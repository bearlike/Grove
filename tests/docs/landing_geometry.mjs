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
// The console is a cabinet and a screen and nothing else: it sits at the
// edge of the shot, so it carries no instruments to draw or to animate.
assert.ok(orchestration.getObjectByName('console-screen'), 'missing console-screen');
const consoleMeshes = [];
orchestration.traverse(part => { if (part.isMesh) consoleMeshes.push(part); });
assert.ok(consoleMeshes.length <= 4, `the console is simple shapes, found ${consoleMeshes.length} meshes`);
assert.ok(consoleMeshes.every(part => part.material.emissiveIntensity === 0), 'nothing on the console glows or pulses');
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
factory.elapsed = 0.5;
factory.updateRobots();
assert.ok(factory.activationEnvelope(crossing.delayedTravel).link < 1, 'the second bolt is still reaching at this moment');
assert.equal(crossing.displayMaterial.map, crossing.logoTexture, 'wait for the second bolt to land before verification');
factory.elapsed = 3;
factory.updateRobots();
assert.equal(crossing.displayMaterial.map, factory.checkTexture, 'a cleared robot with both coils finished must be verified');
// A robot enters with its logo and leaves with the check, every pass. The
// delayed clock wraps 1.2 s after the robot does, and in that window a
// re-entering robot used to carry the previous pass's check into view.
{
  for (const runner of factory.robotRunners) {
    let previousZ = null;
    for (let t = 0; t <= 42; t += 1 / 60) {
      factory.elapsed = t; factory.updateRobots();
      const z = runner.group.position.z;
      const verified = runner.displayMaterial.map === factory.checkTexture;
      if (z < -0.35) assert.ok(!verified, `a robot behind the station shows its logo (t=${t.toFixed(2)}, z=${z.toFixed(2)})`);
      if (previousZ !== null && z < previousZ - TRAVEL) assert.ok(!verified, 'the frame a robot wraps to the entry end, it shows its logo');
      previousZ = z;
    }
  }
}
// Snappy: the check appears within a frame of the second bolt reaching
// full strength, the moment it reads as landed, not when it lets go.
{
  let landed = null, check = null;
  for (let t = 0; t <= 5 && check === null; t += 1 / 120) {
    factory.elapsed = t; factory.updateRobots();
    const { link } = factory.activationEnvelope(crossing.delayedTravel);
    if (landed === null && link >= 1) landed = t;
    if (crossing.displayMaterial.map === factory.checkTexture) check = t;
  }
  assert.ok(landed !== null && check !== null, 'the loop has a landing and a check');
  assert.ok(check - landed <= 1 / 60 + 1e-9, `the check lands within a frame of the bolt, lagged ${((check - landed) * 1000).toFixed(0)} ms`);
}
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
    // The check is the reaction to the second strike: on from the moment
    // that bolt is at full strength, and not a frame before, and only on the
    // pass the strike happened on.
    const expected = runner.delayedTravel >= -0.35 && runner.group.position.z >= -0.35 ? factory.checkTexture : runner.logoTexture;
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
    const { link } = factory.activationEnvelope(runner.delayedTravel);
    const verified = runner.displayMaterial.map === factory.checkTexture;
    const reaching = runner.delayedTravel < -0.35;
    if (runner.arcs[0].visible && reaching) assert.ok(!verified, 'the first strike never verifies');
    if (reaching) assert.ok(!verified, 'no check while the second bolt is still reaching');
    if (verified) assert.ok(!reaching, 'the check only ever follows the second strike landing');
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

// The frame governor paces at 60 fps and starts most machines part-way up
// the quality ladder, earning the rest with measured headroom.
const { FrameGovernor, POLICY } = await import('../../docs/javascripts/factory/frame-governor.js');
// Drives the governor like a display: a refresh every `refresh` ms, each one
// rendered only when the governor says so, each rendered frame taking `cost`
// ms (a frame slower than the refresh occupies the next refreshes too).
// Returns the quality changes with their times.
const drive = (governor, start, until, { refresh = 1000 / 60, cost = 0 } = {}) => {
  const changes = [];
  let rendered = 0;
  let busyUntil = -Infinity;
  for (let time = start; time < until; time += refresh) {
    if (time < busyUntil || !governor.shouldRender(time)) continue;
    rendered += 1;
    busyUntil = time + cost;
    const changed = governor.record(time);
    if (changed) changes.push({ time, ...changed });
  }
  return { changes, fps: rendered / ((until - start) / 1000) };
};
const full = { ceiling: 1.5, floor: 0.6, step: 0.15, samples: 4 };

// Pacing: 60 fps on 60, 120 and 144 Hz alike. The old "16.7 ms since the
// last frame" rule rendered every third 144 Hz refresh, 48 fps.
for (const hz of [60, 120, 144, 165]) {
  const { fps } = drive(new FrameGovernor(full), 0, 10_000, { refresh: 1000 / hz });
  assert.ok(fps > 57 && fps < 62, `${hz} Hz must pace to 60 fps, got ${fps.toFixed(1)}`);
}
// Nobody starts at the top: most visitors are not on high-end hardware,
// and a top start costs them seconds of over-budget frames and a visible
// stair of downgrades. The start is about 80 % of the way up.
{
  const governor = new FrameGovernor(full);
  const bottom = governor.ladder.length - 1;
  assert.ok(governor.level > 0, 'the start is below the top rung');
  assert.ok(Math.abs(1 - governor.level / bottom - POLICY.startShare) <= 0.5 / bottom, `the start is at ${POLICY.startShare} of the ladder`);
  assert.equal(governor.quality.pixelRatio, 1.5, 'and keeps full resolution');
  assert.equal(governor.quality.samples, 2, 'with 2x MSAA, one rung below full');
}
// A capable machine on a 144 Hz panel never loses quality, and climbs the
// rest of the way on its own.
{
  const governor = new FrameGovernor(full);
  const { changes } = drive(governor, 0, 60_000, { refresh: 1000 / 144, cost: 4 });
  assert.ok(changes.every((change, index) => index === 0 || change.samples >= changes[index - 1].samples), 'a fast machine at 144 Hz only climbs');
  assert.equal(governor.level, 0, 'and reaches the top');
}
// Page load is not a verdict: a stall in the warm-up changes nothing.
{
  const governor = new FrameGovernor(full);
  const start = governor.level;
  drive(governor, 0, 2000, { cost: 400 });
  const during = drive(governor, 2000, 2400).changes;
  assert.deepEqual(during, [], 'slow frames during warm-up cost nothing');
  assert.equal(governor.level, start);
}
// One slow second is a hiccup; two in a row is a verdict.
{
  const governor = new FrameGovernor(full);
  drive(governor, 0, 4000);
  assert.deepEqual(drive(governor, 4000, 5100, { cost: 45 }).changes, [], 'one slow second changes nothing');
  const after = drive(governor, 5100, 9000).changes;
  assert.ok(after.every(change => governor.ladder.indexOf(governor.quality) <= governor.ladder.findIndex(rung => rung.samples === change.samples && rung.pixelRatio === change.pixelRatio && rung.shadow === change.shadow)), 'and is forgotten once frames are on time again: only climbs follow');
  assert.ok(governor.level <= Math.round((governor.ladder.length - 1) * (1 - POLICY.startShare)), 'never below the start');
}
// Without GPU timing (Firefox, Safari), frame intervals decide: a 2 fps
// machine is rescued within warm-up plus two windows and walks the ladder in
// order to the floor.
{
  const governor = new FrameGovernor(full);
  const { changes } = drive(governor, 0, 120_000, { cost: 500 });
  assert.ok(changes[0].time <= POLICY.warmUpMs + 2 * POLICY.windowMs + 1000, `the first drop must come promptly, came at ${changes[0].time} ms`);
  assert.deepEqual(changes.slice(0, 3).map(({ pixelRatio, samples, shadow }) => [pixelRatio, samples, shadow]), [[1.35, 2, 1024], [1.2, 2, 1024], [1.05, 2, 1024]], 'from the 80 % start (2x MSAA, small shadow map) resolution steps down keeping 2x');
  assert.deepEqual({ ...governor.quality }, { pixelRatio: 0.6, samples: 0, shadow: 1024 }, 'sustained slow frames settle at the floor');
  // Headroom earns quality back, one rung at a time...
  const back = drive(governor, 120_000, 140_000).changes;
  assert.ok(back.length >= 1 && governor.level < governor.ladder.length - 1, 'on-time frames restore quality');
  // ...and a climb that cannot hold is undone and retried later, not at once.
  const undone = new FrameGovernor(full);
  drive(undone, 0, 60_000, { cost: 500 });
  const climb = drive(undone, 60_000, 60_000 + POLICY.climbAfterMs + 1500).changes;
  assert.equal(climb.length, 1, 'one climb');
  const fall = drive(undone, 64_600, 70_000, { cost: 30 }).changes;
  assert.equal(fall.length, 1, 'a climb that cannot hold frame rate is undone');
  const retry = drive(undone, 70_000, 90_000).changes;
  assert.ok(retry.length && retry[0].time - 70_000 >= 2 * POLICY.climbAfterMs - 100, 'after a failed climb the governor waits twice as long');
}
// Headroom is judged against what the pacer can deliver, not a perfect 60:
// a machine holding 55 fps on a jittery panel still earns quality back.
{
  const governor = new FrameGovernor(full);
  drive(governor, 0, 60_000, { cost: 500 });
  const level = governor.level;
  drive(governor, 60_000, 80_000, { refresh: 1000 / 55 });
  assert.ok(governor.level < level, 'a steady 55 fps is headroom');
}
// A software rasterizer starts at the bottom.
{
  const software = new FrameGovernor({ ...full, software: true });
  assert.deepEqual({ ...software.quality }, { pixelRatio: 0.6, samples: 0, shadow: 1024 }, 'a software renderer starts at the floor without MSAA');
}

// Budgets per display.
const hero = (w, h) => 0.62 * w * h;
const at = (dpr, css) => FrameGovernor.range({ devicePixelRatio: dpr, cssPixels: css, maxSamples: 4 });
assert.equal(at(1, hero(2560, 1440)).ceiling, 1, '1440p at 1x renders at native density');
assert.equal(at(1, hero(2560, 1440)).samples, 4, 'with 4x MSAA on the scene');
assert.equal(at(2, hero(1512, 982)).ceiling, 2, 'a 14-inch HiDPI laptop renders at native density');
assert.equal(at(1.5, hero(2560, 1440)).ceiling, 1.5, 'a 4K panel at 150 % renders at native density');
assert.equal(at(1.5, hero(2560, 1440)).samples, 2, 'and trades 4x MSAA for 2x to stay in the sample budget');
assert.equal(at(3, 390 * 219).ceiling, 3, 'a 3x phone renders its band at native density');
assert.equal(at(4, 390 * 219).ceiling, 3, 'density is capped at 3');
assert.equal(at(1, 390 * 219).ceiling, 1, 'never above the display density');
assert.equal(FrameGovernor.range({ devicePixelRatio: 1, cssPixels: 1e6, maxSamples: 0 }).samples, 0, 'no MSAA where the context has none');
for (const [dpr, css] of [[1, hero(2560, 1440)], [2, hero(1512, 982)], [1.5, hero(2560, 1440)], [2, hero(3840, 2160)]]) {
  const { ceiling, samples } = at(dpr, css);
  assert.ok(css * ceiling ** 2 * samples <= POLICY.sampleBudget, `within the sample budget at ${dpr}x`);
}

// The ladder gives up the most cost for the least visible loss first, and
// keeps MSAA on the robots' and coils' thin moving edges until the bottom.
const ladder = FrameGovernor.ladder({ ceiling: 1.19, floor: 0.71, step: 0.12, samples: 4 });
assert.deepEqual(ladder.slice(0, 3), [
  { pixelRatio: 1.19, samples: 4, shadow: 2048 },
  { pixelRatio: 1.19, samples: 2, shadow: 2048 },
  { pixelRatio: 1.19, samples: 2, shadow: 1024 },
], 'MSAA halves, then the shadow map halves, all at full resolution');
assert.ok(ladder.slice(0, -1).every(rung => rung.samples >= 2), 'MSAA survives every rung but the last');
assert.deepEqual(ladder.at(-2), { pixelRatio: 0.71, samples: 2, shadow: 1024 }, 'resolution reaches its floor with 2x MSAA');
assert.deepEqual(ladder.at(-1), { pixelRatio: 0.71, samples: 0, shadow: 1024 });
assert.ok(ladder.filter(rung => rung.pixelRatio < 1.19).every(rung => rung.shadow === 1024), 'resolution is never traded before the shadow map');
assert.ok(FrameGovernor.ladder({ ceiling: 1, floor: 0.6, step: 0.1, samples: 0 }).every(rung => rung.samples === 0), 'a context without MSAA gets a ladder without MSAA');

// Replays the reported machine: GPU ms per rung modelled from its dump, a
// 60 Hz grid. The dump had the focus pass at 14 ms; it is gone.
const cost = ({ pixelRatio, samples, shadow }) => (3.9 + (samples === 4 ? 5 : samples === 2 ? 2.5 : 0)) * (pixelRatio / 0.79) ** 2 + (shadow === 2048 ? 1.5 : 0.4);
const replay = (governor, start, until, gpuOf = cost) => {
  const changes = [];
  for (let time = start; time < until; time += 1000 / 60) {
    if (!governor.shouldRender(time)) continue;
    const ms = gpuOf(governor.quality);
    governor.measure([ms]);
    // A frame over 16.7 ms of GPU delays the next one.
    if (ms > 16.7) time += ms - 16.7;
    const changed = governor.record(time);
    if (changed) changes.push({ time, ...changed });
  }
  return changes;
};
{
  const range = { ceiling: 1.19, floor: 0.71, step: 0.12, samples: 4 };
  const governor = new FrameGovernor(range);
  const changes = replay(governor, 0, 60_000);
  const settled = governor.quality;
  // Leaves about a third of the 16.7 ms frame for the compositor and page.
  assert.ok(cost(settled) <= 11, `settles within the GPU budget, at ${cost(settled).toFixed(1)} ms`);
  assert.ok(settled.samples >= 2, 'keeps MSAA on the thin moving edges');
  const best = governor.ladder.findIndex(rung => cost(rung) <= 11);
  assert.equal(governor.level, best, `settles on the best rung that fits (${JSON.stringify(governor.ladder[best])})`);
  assert.ok(changes.at(-1).time <= POLICY.warmUpMs + 4000, `from the 80 % start it settles within a few seconds, took ${Math.round(changes.at(-1).time)} ms`);
  assert.ok(changes.every(change => change.time < 12_000), 'and then stays put: no retry dips');
  // The GPU gets faster (a background load ends): quality comes back.
  const faster = replay(governor, 60_000, 90_000, rung => cost(rung) * 0.4);
  assert.ok(faster.length >= 1, 'a clearly faster GPU earns quality back');
  // Unchanged GPU: a failed rung is never retried.
  const steady = new FrameGovernor(range);
  replay(steady, 0, 30_000);
  assert.deepEqual(replay(steady, 30_000, 120_000), [], 'an unchanged GPU never retries a rung that failed');
  // A GPU with room climbs to the top and no further.
  const strong = new FrameGovernor(range);
  replay(strong, 0, 30_000, rung => cost(rung) * 0.3);
  assert.equal(strong.level, 0, 'a strong GPU climbs to the top rung');
}
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

// A quality change reallocates buffers, one long frame. It waits for a
// moment nobody is watching: never while an arc is firing.
{
  const applied = [];
  factory.applyQuality = quality => applied.push(quality);
  factory.pendingQuality = null;
  factory.gpuTimer = { begin() {}, end() {}, poll: () => [] };
  factory.composer = { render() {} };
  factory.stats = { frames: 0, changes: 0, gpuMs: null, quality: null };
  const rung = { pixelRatio: 1, samples: 2, shadow: 1024 };
  let verdict = null;
  factory.governor = { shouldRender: () => true, measure() {}, record: () => { const v = verdict; verdict = null; return v; } };
  factory.paused = false; factory.visible = true; document.hidden = false;
  // Find an elapsed time with an arc lit and one with none.
  const lit = [], dark = [];
  for (let elapsed = 0; elapsed <= 20; elapsed += 0.05) {
    factory.elapsed = elapsed; factory.updateRobots();
    (factory.arcFiring() ? lit : dark).push(elapsed);
  }
  assert.ok(lit.length && dark.length, 'the loop has both arc and no-arc moments');
  factory.elapsed = lit[0]; factory.lastTime = 0;
  verdict = rung;
  factory.render(0);
  assert.deepEqual(applied, [], 'a verdict during an arc is held back');
  assert.equal(factory.pendingQuality, rung, 'and remembered');
  factory.elapsed = dark.find(t => t > lit[0]) - 0.016; factory.lastTime = 0;
  factory.render(16);
  assert.deepEqual(applied, [rung], 'and applied once the arc has released');
  assert.equal(factory.pendingQuality, null);
}
console.log('Robot geometry, conveyor geometry, synchronized motion, screen visibility, and lifecycle passed');
