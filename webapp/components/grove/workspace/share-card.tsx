"use client";

import { useEffect, useId, useState } from "react";
import { CopyIcon, GlobeIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import type { components } from "@/lib/grove/api/types.gen";
import { SectionCard } from "@/components/grove/card";
import { primarySessionId } from "@/lib/grove/adapters";
import {
  useActivityStream,
  useSaveSharePolicy,
  useSharePolicy,
  useUpdateWorkspace,
  useWorkspacePeek,
} from "@/lib/grove/hooks";

const TTL_CHOICES = [
  { value: "86400", label: "1 day", seconds: 86_400 },
  { value: "604800", label: "7 days", seconds: 604_800 },
  { value: "2592000", label: "30 days", seconds: 2_592_000 },
  { value: "never", label: "Never", seconds: null },
] as const;

type TtlChoice = (typeof TTL_CHOICES)[number];
type SharePolicyUpdateRequest =
  components["schemas"]["SharePolicyUpdateRequest"];

/** The wire endpoint replaces both fields, never just the one a control changed. */
export function sharePolicyRequest(
  ttlSeconds: number | null,
  passcode: string | null,
): SharePolicyUpdateRequest {
  return { ttl_seconds: ttlSeconds, passcode };
}

/** A live link can move only when an operator explicitly names its new session. */
export function needsShareSessionPin(
  shareSessionId: string | null,
  currentSessionId: string | null,
): boolean {
  return currentSessionId !== null && shareSessionId !== currentSessionId;
}

/** The workspace's public read-only link and its project's share policy. */
export function ShareCard({ workspaceId }: { workspaceId: string }) {
  const peek = useWorkspacePeek(workspaceId);
  const { snapshot } = useActivityStream();
  const update = useUpdateWorkspace(workspaceId);
  const [origin, setOrigin] = useState<string | null>(null);
  const [revokeOpen, setRevokeOpen] = useState(false);

  // `window` is unavailable during the route's server render. Delaying only
  // the origin keeps the initial client tree identical, while the token itself
  // remains available from the ordinary workspace query.
  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);

  const token = peek.data?.state.share_token ?? null;
  const url = token && origin ? `${origin}/public/${token}` : null;
  const error = update.error;
  const repoRoot = peek.data?.state.repo_root ?? null;
  const shareSessionId = peek.data?.state.share_session_id ?? null;
  const currentSessionId = primarySessionId(snapshot, workspaceId);

  const revoke = () => {
    update.mutate(
      { share: false },
      {
        onSuccess: () => setRevokeOpen(false),
      },
    );
  };

  const pinCurrentSession = () => {
    if (!currentSessionId) return;
    update.mutate({ share: true, share_session_id: currentSessionId });
  };

  return (
    <>
      <SectionCard
        icon={<GlobeIcon />}
        title="Public link"
        description={
          token
            ? "Anyone with the link can view this workspace read-only."
            : "Share a read-only view of Info, Changes, and the live transcript; never the terminal, files, or controls."
        }
        className="@xl:col-span-2"
      >
        {token ? (
          <>
            {url ? (
              <div className="flex min-w-0 gap-2">
                <Input
                  readOnly
                  value={url}
                  aria-label="Public workspace link"
                  onFocus={(event) => event.currentTarget.select()}
                  className="truncate font-mono text-xs"
                />
                <CopyButton text={url} />
              </div>
            ) : (
              <p className="text-xs text-content-tertiary">
                Preparing public link…
              </p>
            )}
            <ShareTranscript
              shareSessionId={shareSessionId}
              currentSessionId={currentSessionId}
              pending={update.isPending}
              onPinCurrentSession={pinCurrentSession}
            />
            <div>
              <Button
                type="button"
                size="sm"
                variant="destructive"
                disabled={update.isPending}
                onClick={() => setRevokeOpen(true)}
              >
                Stop sharing
              </Button>
            </div>
          </>
        ) : (
          <div>
            <Button
              type="button"
              size="sm"
              disabled={update.isPending}
              onClick={() => update.mutate({ share: true })}
            >
              <GlobeIcon aria-hidden />
              Share workspace
            </Button>
          </div>
        )}
        {!revokeOpen && error && (
          <p role="status" className="text-xs text-destructive">
            {error.message}
          </p>
        )}
      </SectionCard>

      {repoRoot ? <SharePolicy repoRoot={repoRoot} /> : <SharePolicySkeleton />}

      <Dialog open={revokeOpen} onOpenChange={setRevokeOpen}>
        <DialogContent data-testid="share-revoke-dialog">
          <DialogHeader>
            <DialogTitle>Stop sharing this workspace?</DialogTitle>
            <DialogDescription>
              This permanently disables this link. Sharing again creates a
              different link.
            </DialogDescription>
          </DialogHeader>
          {error && (
            <p role="status" className="text-xs text-destructive">
              {error.message}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setRevokeOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={update.isPending}
              onClick={revoke}
              data-testid="share-revoke-confirm"
            >
              Stop sharing
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** One clipboard affordance for a paste-ready string. */
export function CopyButton({ text }: { text: string }) {
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      onClick={() => {
        void navigator.clipboard.writeText(text);
      }}
    >
      <CopyIcon aria-hidden />
      Copy
    </Button>
  );
}

/** The public link's transcript identity, separated from its URL and policy. */
function ShareTranscript({
  shareSessionId,
  currentSessionId,
  pending,
  onPinCurrentSession,
}: {
  shareSessionId: string | null;
  currentSessionId: string | null;
  pending: boolean;
  onPinCurrentSession: () => void;
}): React.ReactNode {
  const needsPin = needsShareSessionPin(shareSessionId, currentSessionId);

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {shareSessionId ? (
        <p className="text-xs text-content-tertiary">
          Published transcript:{" "}
          <span
            className="font-mono text-content-primary"
            title={shareSessionId}
          >
            {shareSessionId.slice(0, 8)}
          </span>
        </p>
      ) : (
        <p className="text-xs text-content-tertiary">
          This link follows the workspace&apos;s current session.
        </p>
      )}
      {needsPin && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-xs text-content-secondary">
            {shareSessionId
              ? "This link is showing an older session."
              : "Pin the current session so this link stays on its shared transcript."}
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={pending}
            onClick={onPinCurrentSession}
          >
            Pin current session
          </Button>
        </div>
      )}
    </div>
  );
}

/** Project-wide expiry and passcode controls sit with the link they govern. */
function SharePolicy({ repoRoot }: { repoRoot: string }) {
  const policy = useSharePolicy(repoRoot);
  const save = useSaveSharePolicy(repoRoot);
  const [selectedTtl, setSelectedTtl] = useState<TtlChoice["value"] | null>(
    null,
  );
  const [passcodeOpen, setPasscodeOpen] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [passcodeSaveError, setPasscodeSaveError] = useState<Error | null>(
    null,
  );

  if (policy.isPending && !policy.data) return <SharePolicySkeleton />;

  if (policy.isError) {
    return (
      <SectionCard
        icon={<GlobeIcon />}
        title="Project share policy"
        description="Both settings apply to every shared workspace in this project, not just this one."
        className="@xl:col-span-2"
      >
        <p role="status" className="text-xs text-destructive">
          Could not load the project share policy: {policy.error.message}
        </p>
        <div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={policy.isFetching}
            onClick={() => void policy.refetch()}
          >
            Retry
          </Button>
        </div>
      </SectionCard>
    );
  }

  const current = policy.data;
  if (!current) return <SharePolicySkeleton />;

  const saveTtl = (choice: TtlChoice) => {
    setSelectedTtl(choice.value);
    save.mutate(sharePolicyRequest(choice.seconds, null), {
      onSettled: () => setSelectedTtl(null),
    });
  };

  return (
    <>
      <SectionCard
        icon={<GlobeIcon />}
        title="Project share policy"
        description="Both settings apply to every shared workspace in this project, not just this one."
        className="@xl:col-span-2"
        data-testid="share-policy-card"
      >
        <div className="flex min-w-0 flex-col gap-1">
          <Label htmlFor="share-policy-ttl">Link expiry</Label>
          <Select
            value={selectedTtl ?? ttlValue(current.ttl_seconds)}
            disabled={save.isPending || current.passcode_set}
            onValueChange={(value) => saveTtl(ttlChoice(value))}
          >
            <SelectTrigger id="share-policy-ttl" size="sm" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TTL_CHOICES.map((choice) => (
                <SelectItem key={choice.value} value={choice.value}>
                  {choice.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-content-tertiary">
            Applies only to links minted after this change; existing links keep
            their stamped expiry.
          </p>
          {current.passcode_set && (
            <p className="text-xs text-content-tertiary">
              Change or clear the passcode before changing expiry, so this full
              policy save cannot remove it.
            </p>
          )}
        </div>

        <div className="flex min-w-0 flex-col gap-1">
          <Label>Passcode</Label>
          <p className="text-xs text-content-tertiary">
            {current.passcode_set
              ? "A passcode is set. Changing it applies immediately to every existing shared link."
              : "No passcode is set. A passcode change applies immediately to every existing shared link."}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant={current.passcode_set ? "outline" : "default"}
              disabled={save.isPending}
              onClick={() => {
                setPasscodeSaveError(null);
                setPasscodeOpen(true);
              }}
            >
              {current.passcode_set ? "Change passcode" : "Set passcode"}
            </Button>
            {current.passcode_set && (
              <Button
                type="button"
                size="sm"
                variant="destructive"
                disabled={save.isPending}
                onClick={() => setClearOpen(true)}
              >
                Clear passcode
              </Button>
            )}
          </div>
        </div>

        {save.isPending && (
          <p role="status" className="text-xs text-content-tertiary">
            Saving project share policy…
          </p>
        )}
        {save.error && (
          <p role="status" className="text-xs text-destructive">
            {save.error.message}
          </p>
        )}
      </SectionCard>

      <PasscodeDialog
        open={passcodeOpen}
        onOpenChange={setPasscodeOpen}
        saving={save.isPending}
        error={passcodeSaveError}
        onSave={(passcode) => {
          setPasscodeSaveError(null);
          save.mutate(
            sharePolicyRequest(current.ttl_seconds ?? null, passcode),
            {
              onError: setPasscodeSaveError,
              onSuccess: () => setPasscodeOpen(false),
            },
          );
        }}
      />
      <Dialog open={clearOpen} onOpenChange={setClearOpen}>
        <DialogContent data-testid="share-passcode-clear-dialog">
          <DialogHeader>
            <DialogTitle>Clear this project&apos;s passcode?</DialogTitle>
            <DialogDescription>
              Every existing shared link will stop requiring it immediately.
            </DialogDescription>
          </DialogHeader>
          {save.error && (
            <p role="status" className="text-xs text-destructive">
              {save.error.message}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setClearOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={save.isPending}
              data-testid="share-passcode-clear-confirm"
              onClick={() =>
                save.mutate(
                  sharePolicyRequest(current.ttl_seconds ?? null, null),
                  { onSuccess: () => setClearOpen(false) },
                )
              }
            >
              Clear passcode
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** A passcode is write-only, so setting and changing it use the same form. */
function PasscodeDialog({
  open,
  onOpenChange,
  saving,
  error,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  saving: boolean;
  error: Error | null;
  onSave: (passcode: string) => void;
}) {
  const inputId = useId();
  const [passcode, setPasscode] = useState("");
  const empty = passcode.trim() === "";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setPasscode("");
        onOpenChange(next);
      }}
    >
      <DialogContent data-testid="share-passcode-dialog">
        <DialogHeader>
          <DialogTitle>Set project passcode</DialogTitle>
          <DialogDescription>
            It is not shown again. The new passcode applies immediately to every
            existing shared link.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!empty) onSave(passcode);
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={inputId}>Passcode</Label>
            <Input
              id={inputId}
              type="password"
              autoComplete="new-password"
              autoFocus
              value={passcode}
              onChange={(event) => setPasscode(event.target.value)}
              data-testid="share-passcode-input"
            />
          </div>
          {error && (
            <p role="status" className="text-xs text-destructive">
              {error.message}
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={saving || empty}
              data-testid="share-passcode-save"
            >
              Save passcode
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ttlValue(
  seconds: number | null | undefined,
): TtlChoice["value"] {
  return (
    TTL_CHOICES.find((choice) => choice.seconds === (seconds ?? null))?.value ??
    "never"
  );
}

export function ttlChoice(value: string): TtlChoice {
  return TTL_CHOICES.find((choice) => choice.value === value) ?? TTL_CHOICES[3];
}

function SharePolicySkeleton() {
  return (
    <SectionCard
      icon={<GlobeIcon />}
      title="Project share policy"
      description="Both settings apply to every shared workspace in this project, not just this one."
      className="@xl:col-span-2"
    >
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-32" />
    </SectionCard>
  );
}
