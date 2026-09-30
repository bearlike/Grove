// Readable entry for the landing page's factory scene. The page loads the
// minified bundle built from this file (grove-factory.min.js, see
// tools/landing_bundle.py); this module is the source of truth and the
// geometry probe imports it directly.
import { FactoryScene } from "./factory/scene.js";

export { FactoryScene };
export { TRAVEL } from "./factory/palette.js";

const landing = document.querySelector("[data-grove-landing]");
const scene = landing?.querySelector(".grove-factory-scene");
if (scene) {
  // Resolve against this module, not the page: the scene is reachable from
  // any route and a page-relative path breaks on all but one of them.
  new FactoryScene(scene, { logos: new URL("../logos/agent-displays/", import.meta.url) });
}
