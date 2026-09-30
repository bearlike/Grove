import * as THREE from "../vendor/three.module.js";

// Temperature, not tint. The page's aura is ember, so the light in the
// scene is warm WHITE (the colour of light, not of paint) and every material
// stays neutral steel and graphite, the way the reference's grey cube stays
// grey under an orange sky. Ember is one accent only, the near rail and its
// glow. A rig that was cyan read as a second design system beside the page,
// and one that was all amber tinted the whole machine orange; neither is
// what a warm room looks like.
export const PALETTE = {
  night: 0x050505,
  glyph: new THREE.Color(0xffffff),
  verified: new THREE.Color(0x37d67f),
  ember: 0xff7a45,
  emberDeep: 0xe0562a,
  emberNeon: 0xffa072,
  warmWhite: 0xffeede,
  softWhite: 0xfff6ee,
};

// Robots travel toward the camera along +z. The scanner sits at z = 0, so
// everything before it is "incoming" (agent logo) and everything after is
// "verified" (green checkbox on the face display).
export const BELT_LENGTH = 34;
export const BELT_WIDTH = 6.6;
export const BELT_TOP = 0.41;
export const TRAVEL = BELT_LENGTH / 2 - 3;
// A rider is fully transparent at either end of its run and fully opaque once
// this far in, so the loop's wrap is a fade through darkness, never a pop.
export const FADE_SPAN = 3.5;
// The second coil samples the conveyor clock this many seconds in the past.
export const COIL_DELAY = 1.2;
export const ROBOT_COUNT = 5;
export const AGENT_BADGES = ["claude-code.svg", "codex.svg", "opencode.svg"];
