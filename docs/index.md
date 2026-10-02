---
title: Grove
template: app.html
# The docs theme's CDN libraries (Mermaid, Swiper, Viewer.js…) are unused
# here; docs/hooks/defer_vendor_scripts.py turns them into idle prefetches so
# they warm the cache for the docs pages instead of competing with this one.
defer_vendor_scripts: true
extra_css:
  - stylesheets/grove-landing.css?v=25
---

<div class="grove-landing" data-grove-landing>
  <div class="grove-landing__mesh" aria-hidden="true"><object data="img/landing/factory-mesh.svg?v=5" type="image/svg+xml" tabindex="-1" aria-hidden="true"></object></div>
  <div class="grove-landing__aura" aria-hidden="true"></div>
  <header class="grove-landing__header">
    <a class="grove-landing__brand" href="." aria-label="Grove home">
      <img src="logos/grove-logo.png" alt="" width="34" height="34">
      <span>Grove</span>
    </a>
    <nav class="grove-landing__nav" aria-label="Primary navigation">
      <a class="grove-landing__nav-link" href="home/">Docs</a>
      <a class="grove-landing__nav-link" href="https://github.com/bearlike/Grove/releases">Releases</a>
      <a class="grove-landing__nav-pill" href="https://github.com/bearlike/Grove"><span class="grove-landing__button-icon"><svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.5-1.4-1.3-1.8-1.3-1.8-1.1-.8.1-.8.1-.8 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.4 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.7.2 2.9.1 3.2.7.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.4c0 .3.2.7.8.6A12 12 0 0 0 12 .3z"/></svg></span>GitHub</a>
      <a class="grove-landing__nav-cta" href="home/">Get started</a>
    </nav>
  </header>

  <div class="grove-landing__hero">
    <section class="grove-landing__copy" aria-labelledby="grove-landing-title">
      <h1 id="grove-landing-title">Your team's agents.<br>One software <span class="grove-landing__mark">factory</span>.</h1>
      <ul class="grove-landing__support" aria-label="Works with">
        <li><a href="https://github.com/anthropics/claude-code" title="Claude Code" style="--support-surface: #d77655"><span class="grove-landing__support-window"><img src="logos/support/claude-code.png" alt="Claude Code" width="44" height="44" loading="lazy"></span></a></li>
        <li><a href="https://github.com/openai/codex" title="Codex" style="--support-surface: #fefefe"><span class="grove-landing__support-window"><img src="logos/support/codex.png" alt="Codex" width="44" height="44" loading="lazy"></span></a></li>
        <li><a href="https://opencode.ai" title="OpenCode" style="--support-surface: #111111"><span class="grove-landing__support-window"><img src="logos/support/opencode.png" alt="OpenCode" width="44" height="44" loading="lazy"></span></a></li>
        <li><a href="https://linear.app" title="Linear" style="--support-surface: #3d48bb"><span class="grove-landing__support-window"><img src="logos/support/linear.png" alt="Linear" width="44" height="44" loading="lazy"></span></a></li>
        <li><a href="https://github.com" title="GitHub" style="--support-surface: #0f1218"><span class="grove-landing__support-window"><img src="logos/support/github.png" alt="GitHub" width="44" height="44" loading="lazy"></span></a></li>
        <li><a href="https://about.gitea.com/" title="Gitea" style="--support-surface: #609926"><span class="grove-landing__support-window"><img src="logos/support/gitea.png" alt="Gitea" width="44" height="44" loading="lazy"></span></a></li>
        <li><a href="https://langfuse.com" title="Langfuse" style="--support-surface: #f1f3f8"><span class="grove-landing__support-window"><img src="logos/support/langfuse.png" alt="Langfuse" width="44" height="44" loading="lazy"></span></a></li>
      </ul>
      <p>
        Bring your own coding agents. Give each task a workspace and the tools to guide it. Follow delivery from ticket to trace.
      </p>
      <div class="grove-landing__install" id="install">
        <input type="radio" name="grove-install" id="grove-install-uv" value="uv" checked>
        <input type="radio" name="grove-install" id="grove-install-pipx" value="pipx">
        <input type="radio" name="grove-install" id="grove-install-pip" value="pip">
        <div class="grove-landing__install-tabs" aria-label="Install with">
          <label class="grove-landing__install-tab" for="grove-install-uv">uv</label>
          <label class="grove-landing__install-tab" for="grove-install-pipx">pipx</label>
          <label class="grove-landing__install-tab" for="grove-install-pip">pip</label>
        </div>
        <div class="grove-landing__install-card">
          <pre class="grove-landing__install-panel" data-install="uv"><code><span class="grove-landing__install-tool">uv</span> tool install grove-factory</code><span class="grove-landing__install-note"># recommended</span>
<code><span class="grove-landing__install-tool">uvx</span> --from grove-factory grove</code><span class="grove-landing__install-note"># try it once</span></pre>
          <pre class="grove-landing__install-panel" data-install="pipx"><code><span class="grove-landing__install-tool">pipx</span> install grove-factory</code><span class="grove-landing__install-note"># isolated</span>
<code><span class="grove-landing__install-tool">pipx</span> upgrade grove-factory</code><span class="grove-landing__install-note"># update later</span></pre>
          <pre class="grove-landing__install-panel" data-install="pip"><code><span class="grove-landing__install-tool">pip</span> install --user grove-factory</code><span class="grove-landing__install-note"># Python 3.12+</span>
<code><span class="grove-landing__install-tool">pip</span> install -U grove-factory</code><span class="grove-landing__install-note"># update later</span></pre>
          <div class="grove-landing__install-footer">
            <span>Works on macOS, Linux and WSL2.</span>
            <a href="getting-started/#install">Learn more <span aria-hidden="true">↗</span></a>
          </div>
        </div>
      </div>
      <div class="grove-landing__actions">
        <a class="grove-landing__button grove-landing__button--primary" href="home/"><span class="grove-landing__button-icon"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v15M3 4h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5v15h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3z"/></svg></span>Documentation</a>
        <a class="grove-landing__button grove-landing__button--secondary" href="https://github.com/bearlike/Grove"><span class="grove-landing__button-icon"><svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.5-1.4-1.3-1.8-1.3-1.8-1.1-.8.1-.8.1-.8 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.4 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.7.2 2.9.1 3.2.7.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.4c0 .3.2.7.8.6A12 12 0 0 0 12 .3z"/></svg></span>View on GitHub</a>
      </div>
    </section>

    <div class="grove-landing__visual">
      <div class="grove-factory-scene" aria-label="Claude Code, Codex and OpenCode robots travel between two activation coils. Paired electrical links connect to their antennas, then their displays turn into green check marks." role="img">
        <canvas class="grove-factory-scene__canvas" data-grove-factory-canvas></canvas>
      </div>
    </div>
  </div>
  <div class="grove-landing__grain" aria-hidden="true"></div>
</div>
<script type="module" src="javascripts/grove-factory.min.js?v=8"></script>

<span id="explore-the-docs"></span>
