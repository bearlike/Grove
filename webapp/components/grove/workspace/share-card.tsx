"use client";

import { useEffect, useId, useState } from "react";
import {
  CheckIcon,
  CopyIcon,
  GlobeIcon,
  KeyRoundIcon,
  LinkIcon,
  RefreshCwIcon,
  ShieldOffIcon,
  Trash2Icon,
} from "lucide-react";

import { CardField, CardFields, CardRegion, SectionCard } from "@/components/grove/card";
import { HelpLabel } from "./help-hint";
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
import { primarySessionId } from "@/lib/grove/adapters";
import type { components } from "@/lib/grove/api/types.gen";
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
const FIELD_COLUMN =
  "grid-cols-[72px_minmax(0,1fr)] [&>dd]:overflow-visible [&>dd]:whitespace-normal";
const CONTROL_FOCUS =
  "min-h-[24px] border focus-visible:border-ring focus-visible:ring-ring/50";

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

/** The public workspace link and its project-wide policy share one surface. */
export function ShareCard({ workspaceId }: { workspaceId: string }) {
  const peek = useWorkspacePeek(workspaceId);
  const { snapshot } = useActivityStream();
  const update = useUpdateWorkspace(workspaceId);
  const [origin, setOrigin] = useState<string | null>(null);
  const [revokeOpen, setRevokeOpen] = useState(false);

  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);

  const token = peek.data?.state.share_token ?? null;
  const url = token && origin ? `${origin}/public/${token}` : null;
  const repoRoot = peek.data?.state.repo_root ?? null;
  const shareSessionId = peek.data?.state.share_session_id ?? null;
  const currentSessionId = primarySessionId(snapshot, workspaceId);

  return (
    <>
      <SectionCard
        icon={<GlobeIcon />}
        title="Sharing"
        className="@xl:col-span-2"
        data-testid="share-card"
      >
        <div className="grid min-w-0 gap-3 @lg:grid-cols-2">
          <WorkspaceShare
            token={token}
            url={url}
            error={update.error}
            pending={update.isPending}
            shareSessionId={shareSessionId}
            currentSessionId={currentSessionId}
            onShare={() => update.mutate({ share: true })}
            onRevoke={() => setRevokeOpen(true)}
            onPinCurrentSession={() => {
              if (currentSessionId) {
                update.mutate({
                  share: true,
                  share_session_id: currentSessionId,
                });
              }
            }}
          />
          <div className="border-t border-border pt-3 @lg:border-t-0 @lg:border-l @lg:pt-0 @lg:pl-3">
            {repoRoot ? (
              <SharePolicy repoRoot={repoRoot} />
            ) : (
              <SharePolicySkeleton />
            )}
          </div>
        </div>
      </SectionCard>

      <Dialog open={revokeOpen} onOpenChange={setRevokeOpen}>
        <DialogContent data-testid="share-revoke-dialog">
          <DialogHeader>
            <DialogTitle>Stop sharing this workspace?</DialogTitle>
            <DialogDescription>
              This permanently disables this link. Sharing again creates a
              different link.
            </DialogDescription>
          </DialogHeader>
          {update.error && (
            <p role="status" className="text-xs text-destructive">
              {update.error.message}
            </p>
          )}
          <DialogFooter>
            <Button
              size="xs"
              variant="outline"
              className={CONTROL_FOCUS}
              onClick={() => setRevokeOpen(false)}
            >
              <ShieldOffIcon aria-hidden />
              Cancel
            </Button>
            <Button
              size="xs"
              variant="destructive"
              className={CONTROL_FOCUS}
              disabled={update.isPending}
              onClick={() =>
                update.mutate(
                  { share: false },
                  { onSuccess: () => setRevokeOpen(false) },
                )
              }
              data-testid="share-revoke-confirm"
            >
              <Trash2Icon aria-hidden />
              Stop sharing
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function WorkspaceShare({
  token,
  url,
  error,
  pending,
  shareSessionId,
  currentSessionId,
  onShare,
  onRevoke,
  onPinCurrentSession,
}: {
  token: string | null;
  url: string | null;
  error: Error | null;
  pending: boolean;
  shareSessionId: string | null;
  currentSessionId: string | null;
  onShare: () => void;
  onRevoke: () => void;
  onPinCurrentSession: () => void;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <CardRegion>
        <HelpLabel
          label="This workspace"
          tooltip="Anyone with the link can read Info, Changes, and the live transcript; never the terminal, files, or controls."
        />
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-content-tertiary">
            {token ? "Shared" : "Private"}
          </span>
          {token ? (
            <>
              <ShareTranscript
                shareSessionId={shareSessionId}
                currentSessionId={currentSessionId}
                pending={pending}
                onPinCurrentSession={onPinCurrentSession}
              />
              <Button
                type="button"
                size="xs"
                variant="destructive"
                className={CONTROL_FOCUS}
                disabled={pending}
                onClick={onRevoke}
              >
                <Trash2Icon aria-hidden />
                Revoke
              </Button>
            </>
          ) : (
            <Button
              type="button"
              size="xs"
              className={CONTROL_FOCUS}
              disabled={pending}
              onClick={onShare}
            >
              <LinkIcon aria-hidden />
              Share workspace
            </Button>
          )}
        </div>
      </CardRegion>
      {token && (
        <CardRegion>
          <HelpLabel
            label="Once shared"
            tooltip="This link is read-only. Expiry changes affect new links; passcode changes apply to existing links immediately."
          />
          {url ? (
            <div className="flex min-w-0 items-center gap-2">
              <Input
                readOnly
                value={url}
                aria-label="Public workspace link"
                onFocus={(event) => event.currentTarget.select()}
                className="min-w-0 truncate font-mono text-xs"
              />
              <CopyButton text={url} />
            </div>
          ) : (
            <p className="text-xs text-content-tertiary">Preparing public link…</p>
          )}
        </CardRegion>
      )}
      {error && (
        <p role="status" className="text-xs text-destructive">
          {error.message}
        </p>
      )}
    </div>
  );
}

/** One clipboard affordance for a paste-ready string. */
export function CopyButton({ text }: { text: string }) {
  return (
    <Button
      type="button"
      size="xs"
      variant="outline"
      className={CONTROL_FOCUS}
      onClick={() => {
        void navigator.clipboard.writeText(text);
      }}
    >
      <CopyIcon aria-hidden />
      Copy
    </Button>
  );
}

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
}) {
  const needsPin = needsShareSessionPin(shareSessionId, currentSessionId);
  const sessionLabel = shareSessionId
    ? `Session ${shareSessionId.slice(0, 8)}`
    : "Follows current session";
  const tooltip = shareSessionId
    ? `Published transcript: ${shareSessionId}.`
    : "This legacy link follows the workspace’s current session.";

  return (
    <>
      <span className="font-mono text-xs" title={tooltip}>
        {sessionLabel}
      </span>
      {needsPin && (
        <Button
          type="button"
          size="xs"
          variant="outline"
          className={CONTROL_FOCUS}
          disabled={pending}
          onClick={onPinCurrentSession}
        >
          <CheckIcon aria-hidden />
          Pin current session
        </Button>
      )}
    </>
  );
}

/** Project-wide expiry and passcode controls beside the link they govern. */
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
      <CardRegion>
        <HelpLabel
          label="Project policy"
          tooltip="These settings apply to every shared workspace in this project."
        />
        <p role="status" className="text-xs text-destructive">
          Could not load the project share policy: {policy.error.message}
        </p>
        <div>
          <Button
            type="button"
            size="xs"
            variant="outline"
            className={CONTROL_FOCUS}
            disabled={policy.isFetching}
            onClick={() => void policy.refetch()}
          >
            <RefreshCwIcon aria-hidden />
            Retry
          </Button>
        </div>
      </CardRegion>
    );
  }

  const current = policy.data;
  if (!current) return <SharePolicySkeleton />;

  const saveTtl = (choice: TtlChoice) => {
    setSelectedTtl(choice.value);
    // This is a full replacement endpoint: retaining the passcode is impossible
    // without plaintext, so expiry is disabled while one is set.
    save.mutate(sharePolicyRequest(choice.seconds, null), {
      onSettled: () => setSelectedTtl(null),
    });
  };

  return (
    <>
      <CardRegion>
        <HelpLabel
          label="Project policy"
          tooltip="These settings apply to every shared workspace in this project."
        />
        <CardFields className={FIELD_COLUMN}>
          <CardField
            label={
              <HelpLabel
                inherit
                label="Link expiry"
                tooltip={
                  current.passcode_set
                    ? "Expiry is disabled while a passcode is set because this endpoint replaces both policy fields."
                    : "Applies only to links minted after this change; existing links keep their stamped expiry."
                }
              />
            }
          >
            <Select
              value={selectedTtl ?? ttlValue(current.ttl_seconds)}
              disabled={save.isPending || current.passcode_set}
              onValueChange={(value) => saveTtl(ttlChoice(value))}
            >
              <SelectTrigger
                id="share-policy-ttl"
                size="sm"
                className="min-h-[24px] w-fit"
                aria-label="Link expiry"
              >
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
          </CardField>
          <CardField
            label={
              <HelpLabel
                inherit
                label="Passcode"
                tooltip="Changing or clearing this passcode applies immediately to every existing shared link."
              />
            }
          >
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="xs"
                variant={current.passcode_set ? "outline" : "default"}
                className={CONTROL_FOCUS}
                disabled={save.isPending}
                onClick={() => {
                  setPasscodeSaveError(null);
                  setPasscodeOpen(true);
                }}
              >
                <KeyRoundIcon aria-hidden />
                {current.passcode_set ? "Change passcode" : "Set passcode"}
              </Button>
              {current.passcode_set && (
                <Button
                  type="button"
                  size="xs"
                  variant="destructive"
                  className={CONTROL_FOCUS}
                  disabled={save.isPending}
                  onClick={() => setClearOpen(true)}
                >
                  <Trash2Icon aria-hidden />
                  Clear passcode
                </Button>
              )}
            </div>
          </CardField>
        </CardFields>
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
      </CardRegion>

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
            <Button
              size="xs"
              variant="outline"
              className={CONTROL_FOCUS}
              onClick={() => setClearOpen(false)}
            >
              <ShieldOffIcon aria-hidden />
              Cancel
            </Button>
            <Button
              size="xs"
              variant="destructive"
              className={CONTROL_FOCUS}
              disabled={save.isPending}
              data-testid="share-passcode-clear-confirm"
              onClick={() =>
                save.mutate(
                  sharePolicyRequest(current.ttl_seconds ?? null, null),
                  {
                    onSuccess: () => setClearOpen(false),
                  },
                )
              }
            >
              <Trash2Icon aria-hidden />
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
              size="xs"
              variant="outline"
              className={CONTROL_FOCUS}
              onClick={() => onOpenChange(false)}
            >
              <ShieldOffIcon aria-hidden />
              Cancel
            </Button>
            <Button
              type="submit"
              size="xs"
              className={CONTROL_FOCUS}
              disabled={saving || empty}
              data-testid="share-passcode-save"
            >
              <CheckIcon aria-hidden />
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
    <CardRegion>
      <HelpLabel
        label="Project policy"
        tooltip="These settings apply to every shared workspace in this project."
      />
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-32" />
    </CardRegion>
  );
}
