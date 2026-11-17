import { CompassIcon, GitPullRequestIcon, BugIcon, type LucideIcon } from "lucide-react";

export interface LaunchPreset {
  readonly id: string;
  readonly icon: LucideIcon;
  /** The mark's identity hue (§4.1): one of the chart set, unique per preset. */
  readonly tone: `text-chart-${1 | 2 | 3 | 4 | 5}`;
  readonly title: string;
  readonly description: string;
  /** What "Try" writes into the brief. It is never sent without a second action. */
  readonly prompt: string;
}

/** The one source of the landing page's example briefs. */
export const LAUNCH_PRESETS: readonly LaunchPreset[] = [
  {
    id: "fix-tests",
    icon: BugIcon,
    tone: "text-chart-5",
    title: "Fix the failing tests",
    description: "Find the root cause and prove it with a green run",
    prompt:
      "Run the test suite, find the failing tests, fix the root cause rather than the assertions, and re-run until everything passes.",
  },
  {
    id: "review-branch",
    icon: GitPullRequestIcon,
    tone: "text-chart-4",
    title: "Review my branch",
    description: "Bugs, risky changes and missing tests, by severity",
    prompt:
      "Review the changes on this branch against the base branch. List bugs, risky changes and missing tests ordered by severity, and fix the clear-cut ones.",
  },
  {
    id: "map-codebase",
    icon: CompassIcon,
    tone: "text-chart-2",
    title: "Map this codebase",
    description: "Structure, entry points and how to build and test",
    prompt:
      "Explore this repository and write a short guide to its structure, its entry points, and how to build, test and lint it.",
  },
];
