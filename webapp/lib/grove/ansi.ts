/**
 * Strip ANSI / VT100 SGR escape sequences from a string.
 *
 * The daemon's `agent_snapshot` is `tmux capture-pane -e` output, which
 * includes color/attribute escapes (e.g. `\x1b[38;5;114m`). The TUI feeds
 * those into Rich's `Text.from_ansi()` for color rendering; the webapp
 * is a glance dashboard where structure matters more than color, so we
 * strip them and render plain monospace text. Saves ~25 KB of an
 * ANSI-to-HTML dependency we don't need for V1.
 *
 * Pattern matches CSI sequences (most common — colors, attributes, cursor
 * moves) plus OSC sequences (window title, hyperlinks). Conservative; an
 * escaped byte that isn't part of one of these grammars is left alone.
 */
const ANSI_PATTERN = new RegExp(
  [
    // CSI: ESC [ ... <final byte 0x40-0x7E>
    "\\x1b\\[[0-?]*[ -/]*[@-~]",
    // OSC: ESC ] ... BEL or ST (ESC \)
    "\\x1b\\][^\\x07\\x1b]*(?:\\x07|\\x1b\\\\)",
  ].join("|"),
  "g",
);

export function stripAnsi(s: string): string {
  return s.replace(ANSI_PATTERN, "");
}
