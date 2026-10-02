// Decides whether a display refresh should become a rendered frame, and at
// what quality. Two jobs, both about not doing work nobody can see:
//
// 1. Pace the frame rate at 60. A 120 or 144 Hz panel would otherwise render
//    this scene two or more times per 60 Hz frame for motion that is already
//    smooth at 60.
// 2. Hold 60 fps at the best quality the GPU can afford, and when it cannot,
//    give up what costs the most for the least visible loss first.
//
// Every tunable lives in POLICY with the reason it has that value.
export const POLICY = {
  // Pixels the base canvas may draw. 4 million covers the split hero at
  // native density on a 1440p desktop and a HiDPI laptop, and 1.5x on a 4K
  // panel at 150 %.
  pixelBudget: 4_000_000,
  // Pixels x MSAA samples in the scene target, the one multisampled buffer.
  // A big canvas trades 4x for 2x instead of growing past this.
  sampleBudget: 16_000_000,
  // Never render above this density, and never above the display's own.
  maxPixelRatio: 3,
  // The resolution the canvas may fall back to, as a share of its ceiling,
  // and never below this absolute density.
  floorShare: 0.6,
  minPixelRatio: 0.6,
  // Where a machine starts on the ladder, as a share of the rungs from the
  // bottom (1 is the top). Most visitors are not on high-end hardware, and
  // a start at the top costs every one of them a few seconds of over-budget
  // frames and a visible stair of downgrades. 0.8 is one rung of MSAA below
  // full on the usual ladder; headroom earns the rest back.
  startShare: 0.8,
  // A frame's GPU time must fit this share of the 16.7 ms frame, leaving
  // room for the compositor and the rest of the page.
  gpuBudgetMs: 11,
  // A rung above the current one is tried again only once the GPU has run
  // this much faster than when it failed, so a machine that cannot hold a
  // rung is not pulled back into it every few seconds (measured: a dip to
  // 25 fps every ~9 s on integrated graphics).
  retryMargin: 0.8,
  // Judging speed. A window is one second of wall time, never a frame count.
  // Nothing is judged in the first seconds after start or resume, where
  // shader compiles and texture uploads look like a slow GPU.
  windowMs: 1000,
  warmUpMs: 2500,
  // Without GPU timing: under 40 fps for two windows in a row is too slow,
  // and averaging 52 fps or better is headroom.
  slowInterval: 25,
  slowWindows: 2,
  fastInterval: 1000 / 52,
  climbAfterMs: 3000,
};

export class FrameGovernor {
  constructor({ maxFps = 60, ceiling = 1.5, floor = 0.6, step = 0.15, samples = 4, software = false, policy = POLICY } = {}) {
    this.policy = policy;
    this.interval = 1000 / maxFps;
    this.ladder = FrameGovernor.ladder({ ceiling, floor, step, samples });
    // Start part-way up (see startShare) and let measured headroom earn the
    // rest. A software rasterizer cannot afford any of it: start at the bottom.
    const bottom = this.ladder.length - 1;
    this.level = software ? bottom : Math.round(bottom * (1 - policy.startShare));
    // Per rung: the GPU ms it cost when it was abandoned for being over
    // budget, and the GPU ms it first measured after the rung above failed
    // (null: never). The pair decides when a retry is worth it.
    this.failed = this.ladder.map(() => null);
    this.baseline = this.ladder.map(() => null);
    this.fresh = false;
    this.climbAfter = policy.climbAfterMs;
    this.climbed = false;
    this.due = null;
    this.reset();
  }

  // The quality ceiling for a canvas of `cssPixels` CSS pixels on a display
  // of `devicePixelRatio`: the pixel ratio (native where the budget allows,
  // never under 1.5 or above native) and the MSAA the sample budget allows.
  static range({ devicePixelRatio = 1, cssPixels, maxSamples = 4, policy = POLICY }) {
    const affordable = cssPixels > 0 ? Math.sqrt(policy.pixelBudget / cssPixels) : 1.5;
    const ceiling = +Math.min(devicePixelRatio, policy.maxPixelRatio, Math.max(1.5, affordable)).toFixed(2);
    const floor = +Math.min(ceiling, Math.max(policy.minPixelRatio, ceiling * policy.floorShare)).toFixed(2);
    const drawn = Math.max(1, cssPixels * ceiling * ceiling);
    let samples = maxSamples >= 4 ? 4 : maxSamples >= 2 ? 2 : 0;
    while (samples > 0 && drawn * samples > policy.sampleBudget) samples = samples > 2 ? 2 : 0;
    return { ceiling, floor, step: +Math.max(0.1, (ceiling - floor) / 4).toFixed(2), samples };
  }

  // Top rung first, ordered by what each step saves against what it shows.
  // Measured on integrated graphics at 1440p: MSAA 4x was about 10 ms and
  // resolution 1.19 -> 0.89 saved only 1.6 ms while being the most visible
  // loss. What the eye reads as "low clarity" on this scene is moving thin
  // geometry (face seams, ear grooves, coil windings), which crawls without
  // MSAA and merges when upscaled. So:
  //   1. MSAA halves to 2x;
  //   2. the shadow map halves (a 2048 map is redrawn every frame; the
  //      shadows themselves are soft enough that 1024 reads the same);
  //   3. resolution steps down, still with 2x MSAA;
  //   4. MSAA goes only on the bottom rung.
  static ladder({ ceiling, floor, step, samples }) {
    const keep = Math.min(samples, 2);
    const rungs = [{ pixelRatio: ceiling, samples, shadow: 2048 }];
    if (samples > keep) rungs.push({ pixelRatio: ceiling, samples: keep, shadow: 2048 });
    rungs.push({ pixelRatio: ceiling, samples: keep, shadow: 1024 });
    for (let ratio = ceiling - step; ratio > floor + 0.01; ratio -= step) rungs.push({ pixelRatio: +ratio.toFixed(2), samples: keep, shadow: 1024 });
    if (floor < ceiling) rungs.push({ pixelRatio: floor, samples: keep, shadow: 1024 });
    if (keep > 0) rungs.push({ pixelRatio: floor, samples: 0, shadow: 1024 });
    return rungs;
  }

  get quality() {
    return this.ladder[this.level];
  }

  // Start over after a pause: the gap before the next frame is not a slow
  // frame, and the first seconds back are warm-up again.
  reset() {
    this.due = null;
    this.windowStart = null;
    this.windowFrames = 0;
    this.windowGpu = [];
    this.warmUntil = null;
    this.slowStreak = 0;
    this.fastTime = 0;
  }

  // True when this refresh should render. Frames are due on a fixed 60 Hz
  // grid, so 120 and 144 Hz panels average 60 fps. A "16.7 ms since the last
  // frame" rule renders every third 144 Hz refresh, 48 fps.
  shouldRender(now) {
    return this.due === null || now >= this.due - 3;
  }

  // Report GPU milliseconds for frames rendered at the current rung. They
  // arrive a few frames late; the rare one straddling a rung change is
  // absorbed by the median.
  measure(gpuMs) {
    this.windowGpu.push(...gpuMs);
  }

  // Record a rendered frame. Returns the new quality rung when it changed,
  // otherwise null.
  record(now) {
    // Advance the grid; a machine that fell behind restarts it from now
    // rather than bursting to catch up.
    this.due = this.due === null || now - this.due > this.interval ? now + this.interval : this.due + this.interval;
    if (this.windowStart === null) {
      this.windowStart = now;
      this.windowFrames = 0;
      this.warmUntil ??= now + this.policy.warmUpMs;
      return null;
    }
    this.windowFrames += 1;
    const span = now - this.windowStart;
    if (span < this.policy.windowMs) return null;
    const interval = span / this.windowFrames;
    const gpu = median(this.windowGpu);
    this.windowStart = now;
    this.windowFrames = 0;
    this.windowGpu = [];
    if (now < this.warmUntil) return null;
    if (gpu === null) return this.judgeByInterval(interval, span);
    // The first full window on a rung reached by stepping down is its
    // baseline: what it costs while the rung above does not fit.
    if (this.fresh) {
      this.baseline[this.level] = gpu;
      this.fresh = false;
    }
    return this.judgeByGpu(gpu, interval);
  }

  // With GPU timing, the decision is about cost: over budget steps down at
  // once (one window is enough, since the number is the GPU's own), and the
  // rung above is tried only when it is known, or can be expected, to fit.
  judgeByGpu(gpu, interval) {
    const { policy } = this;
    if (gpu > policy.gpuBudgetMs) {
      this.fastTime = 0;
      if (this.level === this.ladder.length - 1) return null;
      // Remember what the rung cost when it was abandoned, and what the
      // rung below costs from here on, to judge a later retry against.
      this.failed[this.level] = gpu;
      return this.move(1);
    }
    // Frames late while the GPU is within budget: the time goes elsewhere
    // (main thread, compositor, another tab). Quality would not fix that.
    if (this.level === 0 || interval > policy.slowInterval) return null;
    const above = this.failed[this.level - 1];
    // The rung above failed at `above` ms. Its cost scales with this rung's,
    // so retry only once this rung has become cheaper by the same factor it
    // would take for the one above to fit: the GPU got faster (a background
    // load ended), not "a few seconds passed".
    if (above !== null) {
      const baseline = this.baseline[this.level];
      if (baseline === null || gpu > baseline * (policy.gpuBudgetMs / above) * policy.retryMargin) return null;
    }
    this.fastTime += policy.windowMs;
    if (this.fastTime < policy.windowMs * 2) return null;
    return this.move(-1);
  }

  // Without GPU timing, fall back to frame intervals: two slow windows in a
  // row is a verdict, and headroom is climbed with a doubling back-off.
  judgeByInterval(interval, span) {
    const { policy } = this;
    if (interval > policy.slowInterval) {
      this.fastTime = 0;
      this.slowStreak += 1;
      if (this.slowStreak < policy.slowWindows) return null;
      if (this.climbed) this.climbAfter *= 2;
      this.climbed = false;
      return this.level < this.ladder.length - 1 ? this.move(1) : null;
    }
    this.slowStreak = 0;
    this.fastTime = interval <= policy.fastInterval ? this.fastTime + span : 0;
    if (this.fastTime >= policy.windowMs * policy.slowWindows) this.climbed = false;
    if (this.fastTime >= this.climbAfter && this.level > 0) {
      this.climbed = true;
      return this.move(-1);
    }
    return null;
  }

  move(delta) {
    this.level += delta;
    this.fastTime = 0;
    this.slowStreak = 0;
    // Stepping down starts a new baseline; stepping up clears the verdict
    // on the rung just left, so a later failure there is judged afresh.
    this.fresh = delta > 0;
    if (delta < 0) this.failed[this.level] = null;
    // GPU timings still in flight belong to the old rung.
    this.windowGpu = [];
    return this.quality;
  }
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}
