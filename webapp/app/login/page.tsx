"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CircleAlertIcon } from "lucide-react";

import { BrandMark } from "@/components/grove/brand-mark";
import { GitHubIcon } from "@/components/icons/github";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type PairingState =
  | { kind: "idle"; error?: string }
  | { kind: "pending"; challengeId: string; code: string }
  | { kind: "approved" }
  | { kind: "denied"; error: string }
  | { kind: "error"; error: string };
type PairChallenge = { challenge_id: string; code: string };
type PairStatus = { state: "pending" | "approved" | "consumed" | "denied" | "expired" };

const POLL_INTERVAL_MS = 2_000;

export default function LoginPage(): React.ReactNode {
  return <Suspense><LoginForm /></Suspense>;
}

function LoginForm(): React.ReactNode {
  const router = useRouter();
  const search = useSearchParams();
  const next = useMemo(() => {
    const candidate = search.get("next");
    return candidate?.startsWith("/") && !candidate.startsWith("//") ? candidate : "/";
  }, [search]);
  const [label, setLabel] = useState("Browser");
  const [state, setState] = useState<PairingState>({ kind: "idle" });

  useEffect(() => {
    if (state.kind !== "pending") return;
    const timer = window.setInterval(() => void checkPairing(state.challengeId), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [state]);

  useEffect(() => {
    if (state.kind !== "approved") return;
    const timer = window.setTimeout(() => router.replace(next), 300);
    return () => window.clearTimeout(timer);
  }, [next, router, state.kind]);

  async function beginPairing(): Promise<void> {
    const device = label.trim();
    if (!device) {
      setState({ kind: "idle", error: "Enter a device name to continue." });
      return;
    }
    try {
      const response = await fetch("/api/auth/pair", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ label: device }),
      });
      if (!response.ok) throw new Error(`Pairing request failed (${response.status}).`);
      const challenge = await response.json() as PairChallenge;
      setState({ kind: "pending", challengeId: challenge.challenge_id, code: challenge.code });
    } catch (error: unknown) {
      setState({ kind: "error", error: error instanceof Error ? error.message : "Could not start pairing." });
    }
  }

  async function checkPairing(challengeId: string): Promise<void> {
    try {
      const response = await fetch(`/api/auth/pair/${encodeURIComponent(challengeId)}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Pairing check failed (${response.status}).`);
      const status = await response.json() as PairStatus;
      if (status.state === "consumed") setState({ kind: "approved" });
      if (status.state === "denied") setState({ kind: "denied", error: "Pairing was denied." });
      if (status.state === "expired") setState({ kind: "denied", error: "The pairing code expired." });
    } catch (error: unknown) {
      setState({ kind: "error", error: error instanceof Error ? error.message : "Could not check pairing." });
    }
  }

  const error = state.kind === "idle" ? state.error : state.kind === "denied" || state.kind === "error" ? state.error : undefined;
  return (
    <main
      className="flex min-h-dvh flex-col items-center justify-center gap-6 p-6"
      data-testid="login-page"
    >
      {/* The mark stands ALONE here, so it takes a `label` — the one place in
          the app that does. Everywhere else it sits beside the word "Grove"
          and a screen reader would say the name twice.

          Above the card rather than inside its header: this is the product
          identifying itself before the card asks for anything, which is what
          makes the screen read as a front door instead of a form on a blank
          page. `size-12` because it is the only thing above the fold competing
          with the pairing code, and it must not win. */}
      <BrandMark label="Grove" className="size-12" />

      <Card className="w-full max-w-md">
        <CardHeader>
          {/* `text-lg` states a size the vendored `CardTitle` does not: it ships
              `leading-none font-semibold` with NO size class, so this inherited
              the 16px browser root — measured — while the body around it reads
              13px. The ramp puts `text-lg` at 16px too, so this changes no
              pixels today; what it changes is that the size is now a decision
              that tracks §1 instead of an accident that tracks the user agent. */}
          <CardTitle className="text-lg">Pair this device</CardTitle>
          <CardDescription>Approve this browser from the Grove host.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {state.kind === "pending" ? (
            <div className="flex flex-col items-center gap-3">
              <p className="text-sm text-content-secondary">Enter this code on the Grove host.</p>
              {/* The screen's subject, and the one thing on it that is a
                  literal you retype — so mono, and the only `text-3xl` in the
                  app. At 24px against a 16px title and 13px body it still reads
                  as the subject after the ramp. */}
              <output className="font-mono text-3xl">{state.code}</output>
              <Waiting>Waiting for approval on the host…</Waiting>
            </div>
          ) : state.kind === "approved" ? (
            <div className="flex flex-col items-center gap-3">
              <p className="text-sm text-content-secondary">Paired. Opening Grove…</p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <Label htmlFor="device-label">Device name</Label>
              <Input id="device-label" value={label} onChange={(event) => setLabel(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void beginPairing(); }} autoFocus />
              {error && <PairingError>{error}</PairingError>}
            </div>
          )}
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          {state.kind === "pending" && <Button variant="outline" onClick={() => setState({ kind: "idle" })}>Cancel</Button>}
          {(state.kind === "idle" || state.kind === "denied" || state.kind === "error") && <Button onClick={() => void beginPairing()}>Pair this device</Button>}
        </CardFooter>
      </Card>

      <LoginFooter />
    </main>
  );
}

/**
 * Who this is and what you are pairing WITH.
 *
 * THE VERSION IS THE DAEMON'S, READ LIVE, AND THAT IS THE WHOLE POINT. A number
 * compiled into this bundle would describe the web front end, which can be a
 * different release from the process on the other end of the socket — so it
 * would answer a question nobody asked while looking exactly like the answer to
 * the one they did. `/api/version` proxies the daemon's public `/healthz`; when
 * that cannot be reached the line simply does not appear, because "no version"
 * and "some version" are different claims and only one of them is honest here.
 *
 * The GitHub link is the only URL allowed on this screen. Grove is developed on
 * a private forge and that address must never reach a shipped artifact.
 */
function LoginFooter(): React.ReactNode {
  const version = useDaemonVersion();

  return (
    <footer
      className="flex items-center gap-3 text-xs text-content-tertiary"
      data-testid="login-footer"
    >
      <a
        href="https://github.com/bearlike/Grove"
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 underline-offset-2 hover:underline"
      >
        <GitHubIcon aria-hidden className="size-[1em]" />
        GitHub
      </a>
      {version ? (
        // Sans with `tabular-nums`: a version is a quantity you read, not a
        // literal you retype, which is the same call the rail footer makes.
        <span className="tabular-nums" data-testid="login-version">
          Grove {version}
        </span>
      ) : null}
    </footer>
  );
}

/**
 * The daemon's version, or `null` until (and unless) it answers.
 *
 * A plain `useEffect` fetch rather than react-query: this page mounts outside
 * the app's `Providers`, and it is one request that never refetches — the daemon
 * cannot change version under a login screen without restarting, which drops
 * the page anyway.
 */
function useDaemonVersion(): string | null {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch("/api/version", { cache: "no-store" });
        if (!response.ok) return;
        const body = (await response.json()) as { version?: unknown };
        if (!cancelled && typeof body.version === "string") setVersion(body.version);
      } catch {
        // Silent by design: the footer's absence IS the degraded state, and a
        // login screen must never lead with a diagnostic about itself.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return version;
}

/**
 * What this screen is waiting for, said in words.
 *
 * It replaces a `Skeleton className="h-2 w-32"` — a 2px pulsing bar standing in
 * for nothing. A skeleton is the shape of content that is about to arrive, and
 * nothing is about to arrive here: we are polling while a HUMAN walks over to
 * the host and types six characters. There is no shape to stand in for, so the
 * honest primitive is a status line that names the thing being waited on.
 *
 * `role="status"` rather than silence, because the pairing code is already on
 * screen and a reader who cannot see the pulse has no other cue that this
 * screen is still live rather than stuck.
 */
function Waiting({ children }: { children: React.ReactNode }): React.ReactNode {
  return (
    <p role="status" className="text-xs text-content-tertiary">
      {children}
    </p>
  );
}

/**
 * A pairing attempt that failed, denied or expired.
 *
 * §10's shape: the SIGNAL is destructive-toned, the EXPLANATION stays neutral.
 * This was a bare `<p role="alert">` inheriting the 16px root — the largest,
 * loudest text on the screen after the code, and in the same colour as the
 * instructions, so a rejection read like a caption.
 *
 * It carries no action of its own on purpose. Every state that reaches here
 * renders the form underneath with "Pair this device" in the footer, which IS
 * the one action that might fix it; a second button beside the message would be
 * two doors to one room.
 */
function PairingError({ children }: { children: React.ReactNode }): React.ReactNode {
  return (
    <p role="alert" className="flex items-start gap-2 text-sm text-content-secondary">
      <CircleAlertIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-destructive" />
      <span>{children}</span>
    </p>
  );
}
