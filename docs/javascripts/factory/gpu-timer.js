// Measures how long the GPU spends on each frame.
//
// Frame intervals cannot tell the governor what a quality setting costs: a
// frame that is on time on a 60 Hz grid could have taken 3 ms or 15 ms, and
// a slow one could be the tab, the compositor or another page. A timer query
// answers the question directly, a few frames late, without stalling. It is
// Chrome and Edge only (EXT_disjoint_timer_query_webgl2); elsewhere `begin`
// and `end` do nothing and `poll` never reports, and the governor falls back
// to frame intervals.
export class GpuTimer {
  constructor(gl) {
    this.gl = gl;
    this.ext = gl.getExtension("EXT_disjoint_timer_query_webgl2");
    this.pending = [];
    this.active = null;
  }

  get available() {
    return Boolean(this.ext);
  }

  begin() {
    // A query the driver has not answered after a few frames is abandoned
    // rather than piling up behind a lost context or a stalled GPU.
    if (!this.ext || this.active || this.pending.length > 4) return;
    this.active = this.gl.createQuery();
    this.gl.beginQuery(this.ext.TIME_ELAPSED_EXT, this.active);
  }

  end() {
    if (!this.active) return;
    this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);
    this.pending.push(this.active);
    this.active = null;
  }

  // GPU milliseconds of every frame whose result has arrived, oldest first.
  // A disjoint event (clock change, context switch) invalidates everything
  // in flight, so those frames are dropped rather than reported wrong.
  poll() {
    if (!this.ext || !this.pending.length) return [];
    const { gl } = this;
    const disjoint = gl.getParameter(this.ext.GPU_DISJOINT_EXT);
    const results = [];
    while (this.pending.length) {
      const query = this.pending[0];
      if (!disjoint && !gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE)) break;
      if (!disjoint) results.push(gl.getQueryParameter(query, gl.QUERY_RESULT) / 1e6);
      gl.deleteQuery(query);
      this.pending.shift();
    }
    return results;
  }
}
