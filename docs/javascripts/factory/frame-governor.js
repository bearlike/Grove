// Decides whether a display refresh should become a rendered frame, and at
// what quality. Two jobs, both about not doing work nobody can see:
//
// 1. Cap the frame rate. A 120 or 144 Hz panel would otherwise render this
//    scene two or more times per 60 Hz frame for motion that is already
//    smooth at 60.
// 2. Trade image quality for frame rate on weak hardware. The scene is fill
//    bound, not draw bound: measured under a software rasterizer, the 2x
//    multisampled scene target is half of every frame and pixel count is most
//    of the rest, while geometry is about a sixth. So the ladder drops MSAA
//    first, then steps the pixel ratio down, and climbs back the same way.
//
// Decisions are made over windows of wall time, never frame counts: a
// machine drawing two frames a second must be rescued in about a second,
// not after forty slow frames.
export class FrameGovernor {
  constructor({ maxFps = 60, ceiling = 1.5, floor = 0.6, step = 0.15, samples = 2, software = false } = {}) {
    this.minInterval = 1000 / maxFps;
    this.ladder = FrameGovernor.ladder({ ceiling, floor, step, samples });
    // A software rasterizer cannot afford the top rung; start at the bottom
    // and let sustained headroom earn quality back.
    this.level = software ? this.ladder.length - 1 : 0;
    // Headroom needed before climbing a rung. Doubles each time a climb has
    // to be undone, so the quality cannot oscillate, yet a slow second during
    // page load does not cost a capable machine its quality for good.
    this.climbAfter = 4000;
    this.climbed = false;
    this.lastFrame = null;
    this.windowStart = null;
    this.windowFrames = 0;
    this.fastTime = 0;
  }

  // The pixel-ratio range for a canvas of `cssPixels` on a display of
  // `devicePixelRatio`. A flat 1.5 cap was right for the desktop hero and
  // left a 3x phone at half its native sharpness, although the phone's band
  // is a tenth of the desktop's area. So the cap is a pixel budget instead:
  // what the desktop hero draws at 1.5 (about 2 million pixels), never above
  // native, never above 3. Above the old cap the floor is half the ceiling,
  // so a 3x phone that has to shed quality still keeps the old 1.5, and the
  // steps widen so the rescue takes as many seconds as on desktop. At or
  // below 1.5 the range is the desktop's, unchanged.
  static range({ devicePixelRatio = 1, cssPixels, budget = 2_000_000 }) {
    const affordable = cssPixels > 0 ? Math.sqrt(budget / cssPixels) : 1.5;
    const ceiling = +Math.min(devicePixelRatio, 3, Math.max(1.5, affordable)).toFixed(2);
    const floor = ceiling > 1.5 ? +(ceiling / 2).toFixed(2) : 0.6;
    return { ceiling, floor, step: +Math.max(0.15, (ceiling - floor) / 6).toFixed(2) };
  }

  // Top rung first: full MSAA at the ceiling ratio, then the ratio without
  // MSAA, then one step down at a time to the floor.
  static ladder({ ceiling, floor, step, samples }) {
    const rungs = [{ pixelRatio: ceiling, samples }];
    if (samples > 0) rungs.push({ pixelRatio: ceiling, samples: 0 });
    for (let ratio = ceiling - step; ratio > floor + 0.01; ratio -= step) rungs.push({ pixelRatio: +ratio.toFixed(2), samples: 0 });
    if (floor < ceiling) rungs.push({ pixelRatio: floor, samples: 0 });
    return rungs;
  }

  get quality() {
    return this.ladder[this.level];
  }

  // Start over after a pause: the gap before the next frame is not a slow frame.
  reset() {
    this.lastFrame = null;
    this.windowStart = null;
    this.windowFrames = 0;
  }

  // True when this refresh should render. The small slack keeps a 60 Hz
  // display from skipping every other frame on timer jitter.
  shouldRender(now) {
    return this.lastFrame === null || now - this.lastFrame >= this.minInterval - 2;
  }

  // Record a rendered frame. Returns the new quality rung when it changed,
  // otherwise null.
  record(now) {
    this.lastFrame = now;
    if (this.windowStart === null) {
      this.windowStart = now;
      this.windowFrames = 0;
      return null;
    }
    this.windowFrames += 1;
    const span = now - this.windowStart;
    if (span < 1000) return null;
    const interval = span / this.windowFrames;
    this.windowStart = now;
    this.windowFrames = 0;
    // Under ~40 fps for a whole second is too slow; within a tenth of the cap
    // for `climbAfter` is headroom worth spending.
    if (interval > 25) {
      if (this.climbed) this.climbAfter *= 2;
      this.climbed = false;
      this.fastTime = 0;
      return this.level < this.ladder.length - 1 ? this.move(1) : null;
    }
    this.climbed = false;
    this.fastTime = interval < this.minInterval * 1.1 ? this.fastTime + span : 0;
    if (this.fastTime >= this.climbAfter && this.level > 0) {
      this.climbed = true;
      return this.move(-1);
    }
    return null;
  }

  move(delta) {
    this.level += delta;
    this.fastTime = 0;
    return this.quality;
  }
}
