---
title: Grove
template: app.html
extra_css:
  - stylesheets/grove-landing.css?v=13
---

<div class="grove-landing" data-grove-landing>
  <header class="grove-landing__header">
    <a class="grove-landing__brand" href="." aria-label="Grove home">
      <img src="logos/grove-logo.png" alt="" width="34" height="34">
      <span>Grove</span>
    </a>
    <nav class="grove-landing__nav" aria-label="Primary navigation">
      <a href="home/">Docs</a>
      <a href="https://github.com/bearlike/Grove">GitHub</a>
    </nav>
  </header>

  <div class="grove-landing__hero">
    <section class="grove-landing__copy" aria-labelledby="grove-landing-title">
      <h1 id="grove-landing-title">Your team's agents.<br>One software factory.</h1>
      <p>
        Bring the coding agents you already use. Grove gives each one its own workspace on shared machines and shows where every ticket stands.
      </p>
      <ul class="grove-landing__support" aria-label="Works with">
        <li><a href="https://github.com/anthropics/claude-code" title="Claude Code"><img src="logos/support/claude-code.png" alt="Claude Code" width="44" height="44" loading="lazy"></a></li>
        <li><a href="https://github.com/openai/codex" title="Codex"><img src="logos/support/codex.png" alt="Codex" width="44" height="44" loading="lazy"></a></li>
        <li><a href="https://opencode.ai" title="OpenCode"><img src="logos/support/opencode.png" alt="OpenCode" width="44" height="44" loading="lazy"></a></li>
        <li><a href="https://linear.app" title="Linear"><img src="logos/support/linear.png" alt="Linear" width="44" height="44" loading="lazy"></a></li>
        <li><a href="https://github.com" title="GitHub"><img src="logos/support/github.png" alt="GitHub" width="44" height="44" loading="lazy"></a></li>
        <li><a href="https://about.gitea.com/" title="Gitea"><img src="logos/support/gitea.png" alt="Gitea" width="44" height="44" loading="lazy"></a></li>
      </ul>
      <div class="grove-landing__actions">
        <a class="grove-landing__button grove-landing__button--primary" href="home/"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v15M3 4h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5v15h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3z"/></svg>Read the docs</a>
        <a class="grove-landing__button grove-landing__button--secondary" href="https://github.com/bearlike/Grove"><svg aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.5-1.4-1.3-1.8-1.3-1.8-1.1-.8.1-.8.1-.8 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.4 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.7.2 2.9.1 3.2.7.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.4c0 .3.2.7.8.6A12 12 0 0 0 12 .3z"/></svg>View on GitHub</a>
      </div>
    </section>

    <div class="grove-landing__visual">
      <div class="grove-factory-scene" aria-label="Claude Code, Codex and OpenCode robots travel along a metal conveyor through the luminous Grove scanner. Their cyan front-display logos become glowing green checkboxes as they exit the scanner." role="img">
        <canvas class="grove-factory-scene__canvas" data-grove-factory-canvas></canvas>
      </div>
    </div>
  </div>
</div>
<script type="module" src="javascripts/grove-factory.js"></script>

<span id="install"></span>
<span id="explore-the-docs"></span>
