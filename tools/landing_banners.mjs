// Renders the GitHub banners from the built landing page. Driven by
// tools/landing_banners.py, which builds and serves the site and passes SITE
// (its URL) and OUT (the directory to write into).
//
// Both banners are the real page with its chrome hidden, so the typography,
// the aura and the 3D scene are the ones the page ships, and a redesign of
// the landing page is a re-run of this script, not a hand edit. The scene
// is parked at a fixed moment (the bolt landed on one robot, the verified
// one leaving) and rendered at full quality, whatever the governor would
// pick for this machine.
import { chromium } from "../webapp/node_modules/@playwright/test/index.mjs";

const SITE = process.env.SITE;
const OUT = process.env.OUT;
const CHROME = `
  .grove-landing__nav, .grove-landing__install, .grove-landing__actions { display: none !important; }
  /* A separator along the bottom edge: the aura's own solar ramp, sun white
     through amber and ember to burgundy, so the banner ends on the theme
     rather than on black. */
  .grove-landing::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: var(--separator, 8px); z-index: 9; background: linear-gradient(90deg, #ffd98a 0%, #fbb238 18%, #ee8214 38%, #d4520c 58%, #a82a08 78%, #5c0a07 100%); }
`;
// The scene exposes nothing on the page; the banner build patches the entry
// module (see tools/landing_banners.py) so the canvas carries its scene.
const PARK = (pixelRatio) => `
  const scene = document.querySelector("[data-grove-factory-canvas]").__scene;
  scene.elapsed = 1.3;
  scene.updateRobots();
  scene.applyQuality({ pixelRatio: ${pixelRatio}, samples: 4, shadow: 2048 });
`;

const banners = {
  // GitHub's social preview: 1280x640, GitHub's own recommended size. The
  // hero as is, minus the chrome, with the brand enlarged so it reads at
  // thumbnail size. GitHub's repository card then CROPS this 2:1 image to
  // about 2.6:1, showing only the middle ~490 px: measured on the live
  // card, the top 75 px (the brand) and the bottom 75 px (the separator)
  // were cut. So the brand starts below an 80 px safe margin. The separator
  // is a thin line on the bottom EDGE, as it is everywhere else the image
  // is shown whole (link unfurls, the settings preview); a line thick enough
  // to survive that one card's crop was a 98 px block in the full image.
  "github-social.png": {
    width: 1280, height: 640, scale: 1,
    css: `${CHROME}
      .grove-landing__copy p { display: none !important; }
      /* Shown at thumbnail size, so the separator is thicker here. */
      .grove-landing { height: 640px !important; min-height: 0 !important; --safe: 80px; --separator: 18px; }
      /* The supported-tool marks stay, scaled with the headline. */
      .grove-landing__support { --support-tile: 3.1rem !important; gap: 0.65rem !important; margin-top: 1.6rem !important; }
      .grove-landing__header { padding: calc(var(--safe) + 0.6rem) 2.6rem 0 !important; }
      .grove-landing__brand img { width: 4.8rem !important; height: 4.8rem !important; border-radius: 1rem !important; }
      /* The wordmark fills the tile's height and sits on its centre line:
         the display face has a tall ascender, so at line-height 1 the
         lowercase body lands mid-tile instead of low. */
      .grove-landing__brand { font-size: 3.9rem !important; line-height: 1 !important; gap: 1.1rem !important; align-items: center !important; }
      .grove-landing__brand img { margin-top: -0.1rem; }
      /* Centre the headline in the strip instead of lifting it toward the
         header the way the page does for its install card. */
      .grove-landing__hero { padding: 0 !important; }
      .grove-landing__copy { width: 42% !important; max-width: none !important; }
      .grove-landing__copy h1 { font-size: 4.2rem !important; }`,
    park: PARK(1),
  },
  // The README header: a 4:1 strip at 2x, replacing the logo, the title and
  // the row of supported-tool marks. The page's own copy block is hidden and
  // a banner block laid out in its place; the scene is reframed for a strip.
  "readme-banner.png": {
    width: 1600, height: 400, scale: 2,
    css: `${CHROME}
      .grove-landing__copy, .grove-landing__header { display: none !important; }
      /* The page has a 600px minimum height; a 400px strip must not inherit
         it, or the shot is the top two thirds of a taller page. */
      .grove-landing { height: 400px !important; min-height: 0 !important; }
      .grove-landing__visual { inset: -300px -60px -240px 46% !important; clip-path: polygon(18% 0, 100% 0, 100% 100%, 0 100%) !important; -webkit-mask-image: linear-gradient(100deg, transparent 10%, #000 24%) !important; mask-image: linear-gradient(100deg, transparent 10%, #000 24%) !important; }
      .grove-banner { position: absolute; top: 0; bottom: 0; left: 0; width: 44%; z-index: 5; display: flex; flex-direction: column; justify-content: center; padding: 0 0 0 3.6rem; color: #f3ece4; }
      .grove-banner__brand { display: flex; align-items: center; gap: 1.2rem; }
      .grove-banner__brand img { width: 5.6rem; height: 5.6rem; border-radius: 1.2rem; margin-top: -0.1rem; }
      .grove-banner__brand span { font-family: var(--font-display); font-size: 5.3rem; line-height: 1; letter-spacing: -0.01em; text-shadow: 0 1px 2px rgba(40, 10, 0, 0.45), 0 2px 14px rgba(60, 16, 0, 0.35); }
      .grove-banner__tag { font-family: var(--font-display); font-size: 2.05rem; line-height: 1.15; margin: 1.1rem 0 0; opacity: 0.94; text-shadow: 0 1px 2px rgba(40, 10, 0, 0.45); }
      .grove-banner__support { display: flex; gap: 0.6rem; margin: 1.3rem 0 0; list-style: none; padding: 0; }
      .grove-banner__support li { width: 3rem; height: 3rem; border-radius: 0.7rem; display: grid; place-items: center; overflow: hidden; border: 2px solid var(--factory-control-edge); background: var(--support-surface); }
      .grove-banner__support img { width: 112%; max-width: none; height: auto; object-fit: cover; }`,
    compose: () => {
      const support = document.querySelector(".grove-landing__support");
      const banner = document.createElement("div");
      banner.className = "grove-banner";
      banner.innerHTML = `
        <div class="grove-banner__brand"><img src="logos/grove-logo.png" alt=""><span>Grove</span></div>
        <p class="grove-banner__tag">Your team's agents. One software <span class="grove-landing__mark">factory</span>.</p>
        <ul class="grove-banner__support">${[...support.querySelectorAll("li")].map((li) => `<li style="${li.querySelector("a").getAttribute("style")}">${li.querySelector("img").outerHTML}</li>`).join("")}</ul>`;
      document.querySelector("[data-grove-landing]").append(banner);
    },
    park: `${PARK(2)}
      // Pulled back along the same line of sight, no view offset, so the
      // station and the robots either side of it fit a strip at mid height.
      scene.camera.position.set(14, 10.5, 17).multiplyScalar(1.32);
      scene.camera.lookAt(0.4, 1.0, -0.4);
      scene.camera.clearViewOffset();
      scene.camera.updateProjectionMatrix();
      scene.composer.render();`,
  },
};

const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
for (const [file, banner] of Object.entries(banners)) {
  const page = await browser.newPage({ viewport: { width: banner.width, height: banner.height }, deviceScaleFactor: banner.scale });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(SITE, { waitUntil: "load", timeout: 180_000 });
  await page.addStyleTag({ content: banner.css });
  if (banner.compose) await page.evaluate(banner.compose);
  await page.waitForFunction(() => document.querySelector("[data-grove-factory-canvas]")?.dataset.ready === "true", null, { timeout: 240_000 });
  await page.evaluate(banner.park);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/${file}` });
  console.log(`wrote ${file} (${banner.width * banner.scale}x${banner.height * banner.scale})`);
  await page.close();
}
await browser.close();
