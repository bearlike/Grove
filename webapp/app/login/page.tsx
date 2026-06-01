"use client";

/**
 * Login / pairing page — the single browser-side surface for the
 * Bluetooth-style handshake. Composed entirely from shadcn primitives
 * (Card / Input / Button / Alert / Skeleton). Zero bespoke chrome.
 *
 * State machine:
 *   idle      → user types a label, clicks Pair → POST /api/auth/pair
 *   pairing   → display the code, poll /api/auth/pair/[id] every 2s
 *   approved  → cookie has been set server-side; redirect to ?next=
 *   denied    → toast + back to idle
 *   error     → surfaced via the inline Alert
 */
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

type Phase =
  | { kind: "idle"; error?: string }
  | { kind: "pairing"; challengeId: string; code: string }
  | { kind: "approved" }
  | { kind: "error"; message: string };

const POLL_MS = 2_000;

function defaultLabel(): string {
  if (typeof navigator === "undefined") return "Browser";
  const ua = navigator.userAgent || "";
  // Cheap heuristic — pretty enough as a default the user will probably
  // accept. They can always overwrite the field.
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android";
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows";
  return "Browser";
}

export default function LoginPage() {
  // Next.js App Router prerenders pages by default, but useSearchParams
  // forces a CSR bailout. Wrap the searchParams-using component in a
  // Suspense boundary so the build-time prerender succeeds with a fallback
  // and the real param resolution happens client-side.
  return (
    <Suspense fallback={null}>
      <LoginPageInner />
    </Suspense>
  );
}

function LoginPageInner() {
  const router = useRouter();
  const params = useSearchParams();
  const next = useMemo(() => {
    const candidate = params?.get("next");
    if (!candidate) return "/";
    // Defensive: only allow same-origin paths (no `//evil.com` open redirects).
    if (!candidate.startsWith("/") || candidate.startsWith("//")) return "/";
    return candidate;
  }, [params]);

  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [label, setLabel] = useState<string>("");
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Prefill the label on first paint (effect runs client-side only).
  useEffect(() => {
    if (label === "") {
      setLabel(defaultLabel());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Tear down the poll on unmount or phase change.
  useEffect(() => {
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
      pollTimer.current = null;
    };
  }, []);

  async function startPairing(): Promise<void> {
    const trimmed = label.trim();
    if (!trimmed) {
      setPhase({ kind: "idle", error: "Please enter a device name." });
      return;
    }
    try {
      const res = await fetch("/api/auth/pair", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ label: trimmed }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const message = body?.detail?.message ?? `Pair request failed (${res.status})`;
        setPhase({ kind: "error", message });
        return;
      }
      const data = (await res.json()) as { challenge_id: string; code: string };
      setPhase({ kind: "pairing", challengeId: data.challenge_id, code: data.code });
      pollTimer.current = setInterval(() => void poll(data.challenge_id), POLL_MS);
    } catch (err) {
      setPhase({ kind: "error", message: `Network error: ${String(err)}` });
    }
  }

  async function poll(challengeId: string): Promise<void> {
    try {
      const res = await fetch(`/api/auth/pair/${encodeURIComponent(challengeId)}`, {
        method: "GET",
        cache: "no-store",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const message = body?.detail?.message ?? `Pairing failed (${res.status})`;
        if (pollTimer.current) clearInterval(pollTimer.current);
        pollTimer.current = null;
        setPhase({ kind: "error", message });
        return;
      }
      const data = (await res.json()) as { state: string };
      switch (data.state) {
        case "consumed":
          if (pollTimer.current) clearInterval(pollTimer.current);
          pollTimer.current = null;
          setPhase({ kind: "approved" });
          // Tiny delay so the success state is visible before redirect.
          setTimeout(() => router.push(next), 400);
          return;
        case "denied":
          if (pollTimer.current) clearInterval(pollTimer.current);
          pollTimer.current = null;
          setPhase({ kind: "idle", error: "Pairing was denied." });
          return;
        case "expired":
          if (pollTimer.current) clearInterval(pollTimer.current);
          pollTimer.current = null;
          setPhase({ kind: "idle", error: "Code expired. Try again." });
          return;
        default:
          // Pending / approved-but-not-yet-consumed: keep polling.
          return;
      }
    } catch (err) {
      if (pollTimer.current) clearInterval(pollTimer.current);
      pollTimer.current = null;
      setPhase({ kind: "error", message: `Network error: ${String(err)}` });
    }
  }

  function cancel(): void {
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = null;
    setPhase({ kind: "idle" });
  }

  return (
    <main className="grid min-h-dvh place-items-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Pair this device</CardTitle>
          <CardDescription>
            {phase.kind === "pairing"
              ? "Approve the code on the host running Grove."
              : "Grant this browser access to the Grove dashboard."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {phase.kind === "idle" && (
            <div className="space-y-4">
              <label className="block space-y-2">
                <span className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  Device name
                </span>
                <Input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void startPairing();
                  }}
                  placeholder="iPhone, Office Mac, …"
                  autoFocus
                />
              </label>
              {phase.error && (
                <p
                  className="rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 px-3 py-2 text-sm text-[var(--status-error)]"
                  role="alert"
                >
                  {phase.error}
                </p>
              )}
            </div>
          )}

          {phase.kind === "pairing" && (
            <div className="space-y-4 text-center">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  Code
                </p>
                <p className="mt-2 select-all font-mono text-3xl font-semibold tracking-widest">
                  {phase.code}
                </p>
              </div>
              <p className="text-sm text-muted-foreground">
                Open the Grove TUI on the host (or run{" "}
                <code className="rounded bg-muted px-1 font-mono text-xs">
                  grove auth pending
                </code>
                ) and approve the matching code.
              </p>
              <Skeleton className="mx-auto h-2 w-32" />
            </div>
          )}

          {phase.kind === "approved" && (
            <div className="space-y-2 text-center">
              <p className="text-sm font-medium">Paired. Redirecting…</p>
              <Skeleton className="mx-auto h-2 w-32" />
            </div>
          )}

          {phase.kind === "error" && (
            <p
              className="rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 px-3 py-2 text-sm text-[var(--status-error)]"
              role="alert"
            >
              {phase.message}
            </p>
          )}
        </CardContent>
        <CardFooter className="flex justify-end gap-2">
          {phase.kind === "idle" && (
            <Button onClick={() => void startPairing()} disabled={!label.trim()}>
              Pair this device
            </Button>
          )}
          {phase.kind === "pairing" && (
            <Button onClick={cancel} variant="outline">
              Cancel
            </Button>
          )}
          {phase.kind === "error" && (
            <Button onClick={() => setPhase({ kind: "idle" })} variant="outline">
              Try again
            </Button>
          )}
        </CardFooter>
      </Card>
    </main>
  );
}
