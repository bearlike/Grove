"use client";

import { Fragment, type ReactNode } from "react";
import { BanIcon, KeyboardIcon } from "lucide-react";

import { SectionCard } from "@/components/grove/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import type { WorkspaceStatus } from "@/lib/grove/api";
import type { components } from "@/lib/grove/api/types.gen";
import { useInterrupt, useSendKey } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import { HelpLabel } from "./help-hint";

type SendKey = components["schemas"]["SendKey"];

const KEY_LABELS = {
  "C-c": "Ctrl+C",
  Up: "Up",
  Down: "Down",
  Left: "Left",
  Right: "Right",
  Enter: "Enter",
  Tab: "Tab",
  Escape: "Escape",
} satisfies Record<SendKey, string>;

/**
 * What each key looks like on its cap — one or more caps, and a chord is the
 * case that makes this a list rather than a string.
 *
 * WORDS FOR EVERYTHING A GLYPH WOULD ONLY HINT AT. `↵`, `⇥` and `⎋` are the
 * conventional marks and they are also the ones that render as tofu on a font
 * that lacks them, at the size where nobody can tell tofu from a box glyph. The
 * four arrows stay symbols because they are in every UI font, they are the
 * shortest unambiguous thing a 24px cap can hold, and the cluster's own SHAPE
 * says which is which before the glyph does. Every button carries the word in
 * its accessible name regardless.
 */
const KEY_CAPS = {
  "C-c": ["Ctrl", "C"],
  Up: ["↑"],
  Down: ["↓"],
  Left: ["←"],
  Right: ["→"],
  Enter: ["Enter"],
  Tab: ["Tab"],
  Escape: ["Esc"],
} satisfies Record<SendKey, string[]>;

/**
 * The four keys drawn as an inverted T, EVERY CELL PLACED EXPLICITLY.
 *
 * Positioning only `Up` and letting the other three auto-flow is the obvious
 * version and it is wrong: the flow resumes in the cell after `Up`, so the
 * cluster renders `↑ ←` over `↓ →` — four arrows in a shape that means nothing.
 * A grid that is a PICTURE has to say where each cell goes.
 */
const ARROWS = [
  { key: "Up", cell: "col-start-2 row-start-1" },
  { key: "Left", cell: "col-start-1 row-start-2" },
  { key: "Down", cell: "col-start-2 row-start-2" },
  { key: "Right", cell: "col-start-3 row-start-2" },
] as const;
const COMMANDS = ["Enter", "Tab", "Escape", "C-c"] as const;

/** A cap holding one arrow rather than a word takes the larger optical size. */
const SYMBOL_CAPS = new Set(["↑", "↓", "←", "→"]);
const CONTROL_FOCUS =
  "min-h-[24px] border focus-visible:border-ring focus-visible:ring-ring/50";

/** Liveness is necessary, not sufficient: the daemon owns terminal capability. */
export function canSendKeys(status: WorkspaceStatus): boolean {
  return status === "active" || status === "idle";
}

/**
 * One named key at a time, delivered to the workspace terminal.
 *
 * A CARD ON CONTROLS, not a popover in the shell header. It used to sit beside
 * the pane switcher — a lone verb in a strip that otherwise only reports and
 * navigates — and a trigger there can carry neither a title nor an
 * explanation, so the popover existed mostly to supply the chrome a card gets
 * for free. `SectionCard`'s header band is that title and that description,
 * which is why nothing below re-states them.
 *
 * Named keys have no provider semantics; the cancel preset keeps its native
 * channel and stays a separate button rather than a ninth cap. Nothing here
 * claims a RESULT: the daemon answers once the key reaches the pane, and what
 * the application does with it is the application's business — the Terminal
 * tab is where that shows up.
 */
export function SendKeysCard({
  workspaceId,
  status,
  canInterrupt,
  native = false,
}: {
  workspaceId: string;
  status: WorkspaceStatus;
  canInterrupt: boolean;
  /**
   * A Grove-owned session has NO terminal to type into — its pane shows the
   * worker's output and the daemon refuses a named key with 501 — so the key
   * groups are withheld and only the provider-channel cancel remains. The
   * card is still worth mounting for that one control, because a native
   * session is exactly where cancel is a real protocol call rather than a
   * keystroke.
   */
  native?: boolean;
}) {
  const sendKey = useSendKey(workspaceId);
  const interrupt = useInterrupt(workspaceId);
  const pending = sendKey.isPending || interrupt.isPending;
  const error = sendKey.error ?? interrupt.error;
  const keyLabel = sendKey.variables ? KEY_LABELS[sendKey.variables] : null;
  const keysAvailable = canSendKeys(status) && !native;

  if (!keysAvailable && !canInterrupt) return null;

  const cancel = canInterrupt && (
    <Button
      size="xs"
      variant="secondary"
      className={CONTROL_FOCUS}
      disabled={pending}
      onClick={() => {
        sendKey.reset();
        interrupt.mutate();
      }}
      data-testid="chat-interrupt"
    >
      <BanIcon aria-hidden />
      Cancel turn
    </Button>
  );

  if (!keysAvailable) {
    // A native session: no terminal to type into, so the one control left is
    // the provider-channel cancel — stated as such, not as a keyboard.
    return (
      <SectionCard
        icon={<BanIcon />}
        title="Cancel turn"
        className="@xl:col-span-2"
        data-testid="send-keys-card"
        data-native="true"
      >
        <p className="text-xs text-content-tertiary">
          This session is Grove-owned: cancellation goes to the agent's own protocol, and there
          is no terminal to send keys to.
        </p>
        <div aria-busy={pending}>{cancel}</div>
        <p role="status" className="text-xs text-content-tertiary">
          {interrupt.isPending
            ? "Sending cancellation…"
            : interrupt.isSuccess
              ? "Cancellation request delivered."
              : null}
        </p>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>Could not deliver cancellation: {error.message}</AlertDescription>
          </Alert>
        )}
      </SectionCard>
    );
  }

  return (
    <SectionCard
      icon={<KeyboardIcon />}
      title="Send keys"
      className="@xl:col-span-2"
      data-testid="send-keys-card"
    >
      {/* The arrows are a picture and the remaining keys are a list, so each
          gets its own named region rather than wrapping as one undifferentiated
          palette. */}
      <div className="grid gap-2 @lg:grid-cols-2" aria-busy={pending}>
        <div
          role="group"
          aria-label="Navigation"
          className="flex flex-col gap-2"
          data-testid="send-keys-arrows"
        >
          <HelpLabel
            label="Navigation"
            tooltip="Delivers literal arrow keys to the workspace terminal."
          />
          <div className="grid w-fit grid-cols-3 gap-1.5">
            {ARROWS.map(({ key, cell }) => (
              <KeyButton
                key={key}
                send={key}
                className={cell}
                disabled={pending || !keysAvailable}
                onSend={() => {
                  interrupt.reset();
                  sendKey.mutate(key);
                }}
              />
            ))}
          </div>
        </div>
        <div
          role="group"
          aria-label="Editing"
          className="flex flex-col gap-2 border-t border-border pt-2 @lg:border-t-0 @lg:border-l @lg:pt-0 @lg:pl-2"
          data-testid="send-keys-commands"
        >
          <HelpLabel
            label="Editing"
            tooltip="Ctrl+C may exit the agent. Escape is literal terminal input; Cancel turn uses the provider’s cancellation channel."
          />
          <div className="flex flex-wrap gap-1.5">
            {COMMANDS.map((key) => (
              <KeyButton
                key={key}
                send={key}
                disabled={pending || !keysAvailable}
                onSend={() => {
                  interrupt.reset();
                  sendKey.mutate(key);
                }}
              />
            ))}
          </div>
          {cancel}
        </div>
      </div>
      <p role="status" className="text-xs text-content-tertiary">
        {sendKey.isPending
          ? `Sending ${keyLabel}…`
          : interrupt.isPending
            ? "Sending cancellation…"
            : sendKey.isSuccess
              ? `Delivered ${keyLabel}.`
              : interrupt.isSuccess
                ? "Cancellation request delivered."
                : null}
      </p>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>
            Could not deliver {sendKey.error ? keyLabel : "cancellation"}:{" "}
            {error.message}
          </AlertDescription>
        </Alert>
      )}
    </SectionCard>
  );
}

/**
 * ONE KEY OR ONE CHORD, ONE BUTTON, ONE TAB STOP.
 *
 * The caps inside are LABELS, not controls — `Kbd` is a `<kbd>` and is already
 * `pointer-events-none`, so a chord cannot become two things to click, and the
 * whole group is `aria-hidden` behind the button's own worded name. `Ctrl + C`
 * therefore announces once, as "Send Ctrl+C", and delivers one simultaneous
 * chord rather than two sequential keys.
 *
 * A BOUNDED BUTTON, NOT A FLOATING CAP. These used to be `xs` `ghost` buttons,
 * which is a 24px transparent hit area with a keycap sitting on it: nothing
 * said where the target began, and the only visible edge belonged to a label.
 * `outline` at 36px gives the perimeter, the hover, the pressed and the
 * focus-visible ring the vendored variant already owns, and a touch target that
 * is a target.
 */
function KeyButton({
  send,
  disabled,
  onSend,
  className,
}: {
  send: SendKey;
  disabled: boolean;
  onSend: () => void;
  className?: string;
}) {
  const caps = KEY_CAPS[send];
  return (
    <Button
      size="xs"
      variant="outline"
      className={cn(
        `${CONTROL_FOCUS} px-2`,
        className,
      )}
      disabled={disabled}
      onClick={onSend}
      aria-label={`Send ${KEY_LABELS[send]}`}
      data-testid={`send-key-${send}`}
    >
      <KbdGroup aria-hidden className="gap-1">
        {caps.map((cap, index) => (
          <Fragment key={cap}>
            {/* THE `+` IS THE CHORD, and it is deliberately NOT a cap: a
                separator drawn like a key reads as a third key to press. It
                stays in the quiet tier so the two real caps keep the contrast. */}
            {index > 0 && (
              <span className="px-0.5 text-xs text-content-tertiary">+</span>
            )}
            <Cap symbol={SYMBOL_CAPS.has(cap)}>{cap}</Cap>
          </Fragment>
        ))}
      </KbdGroup>
    </Button>
  );
}

/**
 * A keycap that INVERTS AGAINST THE THEME: a dark cap with light lettering in
 * the light theme, a light cap with dark lettering in the dark one.
 *
 * `bg-foreground text-background` is the whole mechanism, and it is why this
 * needs no branch and no second token pair. Those two are the theme's maximum-
 * contrast pair and they SWAP with it by construction, so the inversion is a
 * property of the tokens rather than a rule this file has to remember — and the
 * lettering can never fall below the contrast the theme's own body text has.
 *
 * SCOPED TO THIS CARD, on the call site's `className`. The vendored `Kbd` stays
 * untouched, so a keycap in a tooltip or a command menu still reads as the quiet
 * muted chip it is meant to be there; inverting globally would make every
 * passing mention of a shortcut shout.
 *
 * The nominal ramp shrinks with the density root; these labels keep their
 * explicit 14px/16px minimum while still growing with reader font settings.
 */
function Cap({
  children,
  symbol = false,
}: {
  children: ReactNode;
  symbol?: boolean;
}) {
  return (
    <Kbd
      style={{ fontSize: symbol ? "max(16px, 1.25rem)" : "max(14px, 1.09375rem)" }}
      className={cn(
        "keycap h-6 min-w-6 bg-foreground px-1.5 font-medium text-background",
        symbol ? "text-lg leading-none" : "text-base",
      )}
    >
      {children}
    </Kbd>
  );
}
