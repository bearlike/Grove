"use client";

import {
  CircleCheckIcon,
  CircleDashedIcon,
  CircleDotIcon,
  CircleXIcon,
  FolderPlusIcon,
  FolderSearchIcon,
  GitForkIcon,
  LinkIcon,
  LoaderCircleIcon,
  SquareCheckIcon,
  SquareIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { CardShell } from "@/components/grove/card";
import { LocationLabel, ProjectLabel } from "@/components/grove/entity";
import { HEAD_CELL } from "@/components/grove/table-columns";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
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
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  absolutePathError,
  adoptActionLabel,
  adoptRequests,
  cloneRequest,
  cloneUrlError,
  createRequest,
  destinationPreview,
  filterCandidates,
  folderFromUrl,
  projectNameError,
  selectedPaths,
  selectionSummary,
  type AddProjectMethod,
} from "@/lib/grove/adapters/add-project";
import type { ProjectCandidateView, ProjectView } from "@/lib/grove/api";
import { useAddProject, useProjectCandidates } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";

import { useAddProjectUi, type AddProjectRequestContext } from "./add-project-store";

/**
 * Each method's glyph, title and one-line consequence — the issue's icon table,
 * held in one place so the choice button, the breadcrumb and the step title
 * cannot name a method two ways.
 */
const METHODS: Record<
  AddProjectMethod,
  {
    readonly title: string;
    readonly consequence: string;
    readonly Icon: React.ComponentType<React.SVGProps<SVGSVGElement>>;
  }
> = {
  create: { title: "New project", consequence: "Start an empty git repository", Icon: FolderPlusIcon },
  clone: {
    title: "Clone a repository",
    consequence: "From any Git URL your git can reach",
    Icon: GitForkIcon,
  },
  adopt: {
    title: "Add an existing repository",
    consequence: "Already on disk, not in Grove",
    Icon: FolderSearchIcon,
  },
};

const METHOD_ORDER: readonly AddProjectMethod[] = ["create", "clone", "adopt"];

/** One adopt attempt's outcome, keyed by the path that was asked for. */
interface AddOutcome {
  readonly path: string;
  readonly name: string;
  readonly message: string | null;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function basename(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() ?? path;
}

/** Tell the opener that asked about one registered project. */
function announce(context: AddProjectRequestContext, view: ProjectView): void {
  context.onAdded?.({ repoRoot: view.repo_root, cwd: view.cwd });
}

/**
 * The shared add-project wizard, mounted once in the shell and opened from any
 * surface through `useAddProjectUi`.
 *
 * The wizard body mounts only while open, so Cancel, Escape and a finished add
 * all reset it by unmounting — there is no reset path to keep in sync with the
 * fields.
 */
export function AddProjectDialog(): React.ReactNode {
  const open = useAddProjectUi((s) => s.open);
  const close = useAddProjectUi((s) => s.close);
  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : close())}>
      {open ? <AddProjectWizard onClose={close} /> : null}
    </Dialog>
  );
}

function AddProjectWizard({ onClose }: { onClose: () => void }): React.ReactNode {
  // The opener's context, captured for THIS run of the wizard. A reader may
  // close the dialog while a clone runs (the status line says so) and open it
  // again from somewhere else; the late success must neither select the
  // project in the new opener nor close the new wizard. `alive` is that fence:
  // after unmount the project still arrives through the live project stream,
  // and nothing here acts on it.
  const [context] = useState(() => useAddProjectUi.getState().context);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const candidates = useProjectCandidates(true);
  const add = useAddProject();
  const root = candidates.data?.root ?? null;
  const list = candidates.data?.candidates ?? null;

  const [method, setMethod] = useState<AddProjectMethod | null>(null);
  // Fields live HERE, above every step, so Back and Escape keep what was typed.
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [folder, setFolder] = useState<string | null>(null);
  const [location, setLocation] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [manualPath, setManualPath] = useState("");
  const [attempted, setAttempted] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [outcomes, setOutcomes] = useState<readonly AddOutcome[] | null>(null);

  // An untouched Location follows the daemon's root as it loads; touching it
  // makes it the reader's, and an untouched one is omitted from the request.
  const where = location ?? root ?? "";
  const defaultRoot = root ?? "";
  const cloneFolder = folder ?? folderFromUrl(url);

  const dirty =
    name !== "" ||
    url !== "" ||
    folder !== null ||
    location !== null ||
    filter !== "" ||
    selected.size > 0 ||
    manualPath !== "";

  const choose = (next: AddProjectMethod | null): void => {
    setMethod(next);
    setAttempted(false);
    setSubmitError(null);
  };

  const finishOne = (view: ProjectView): void => {
    if (!alive.current) return;
    announce(context, view);
    onClose();
  };

  const submitCreate = async (): Promise<void> => {
    setAttempted(true);
    if (projectNameError(name) || where.trim() === "") return;
    setSubmitError(null);
    try {
      finishOne(await add.mutateAsync(createRequest(name, where, defaultRoot)));
    } catch (error) {
      if (alive.current) setSubmitError(errorMessage(error));
    }
  };

  const submitClone = async (): Promise<void> => {
    setAttempted(true);
    if (cloneUrlError(url) || projectNameError(cloneFolder) || where.trim() === "") return;
    setSubmitError(null);
    try {
      finishOne(await add.mutateAsync(cloneRequest(url, cloneFolder, where, defaultRoot)));
    } catch (error) {
      if (alive.current) setSubmitError(errorMessage(error));
    }
  };

  /**
   * Adds run ONE AT A TIME: each is a daemon-side config write, and the result
   * rows must say which path failed. A retry replaces only the outcomes it
   * re-ran, so a success is never re-added.
   */
  const runAdopt = async (paths: readonly string[], retry: boolean): Promise<void> => {
    setRunning(true);
    setSubmitError(null);
    const fresh: AddOutcome[] = [];
    const added: ProjectView[] = [];
    const names = new Map((list ?? []).map((c) => [c.path, c.name]));
    for (const request of adoptRequests(paths)) {
      try {
        const view = await add.mutateAsync(request);
        added.push(view);
        if (alive.current) announce(context, view);
        fresh.push({ path: request.path, name: view.repo_name, message: null });
      } catch (error) {
        fresh.push({
          path: request.path,
          name: names.get(request.path) ?? basename(request.path),
          message: errorMessage(error),
        });
      }
    }
    if (!alive.current) return;
    setRunning(false);
    if (!retry && paths.length === 1 && added.length === 1) {
      onClose();
      return;
    }
    if (!retry && paths.length === 1) {
      // One failed add keeps the form, like Create and Clone do.
      setSubmitError(fresh[0]?.message ?? null);
      return;
    }
    setOutcomes((previous) => {
      const retried = new Set(paths);
      const kept = (previous ?? []).filter((o) => !retried.has(o.path));
      return [...kept, ...fresh];
    });
  };

  const adoptPaths = manualPath.trim() !== "" ? [manualPath.trim()] : selectedPaths(list ?? [], selected);

  const submitAdopt = async (): Promise<void> => {
    setAttempted(true);
    if (manualPath.trim() !== "" && absolutePathError(manualPath)) return;
    if (adoptPaths.length === 0) return;
    await runAdopt(adoptPaths, false);
  };

  const pending = add.isPending || running;
  const current = method === null ? null : METHODS[method];
  const TitleIcon = current?.Icon ?? FolderPlusIcon;

  return (
    <DialogContent
      // Bounded to the viewport like every other tall dialog here: on a short
      // window the Existing step ran past both edges, cutting off the title and
      // the action row. The candidate table keeps its own bounded scroll inside.
      className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"
      data-testid="add-project-dialog"
      onEscapeKeyDown={(event) => {
        // Escape steps back once anything is typed, rather than discarding it.
        if (method !== null && dirty && !pending && outcomes === null) {
          event.preventDefault();
          choose(null);
        }
      }}
    >
      <DialogHeader className="gap-3">
        {method !== null ? (
          <Breadcrumb>
            <BreadcrumbList className="text-xs">
              <BreadcrumbItem>
                <BreadcrumbLink asChild>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1"
                    onClick={() => choose(null)}
                    disabled={pending}
                    data-testid="add-project-step-back"
                  >
                    <CircleCheckIcon aria-hidden className="size-[1em] shrink-0" />
                    Add a project
                  </button>
                </BreadcrumbLink>
              </BreadcrumbItem>
              <BreadcrumbSeparator />
              <BreadcrumbItem>
                <BreadcrumbPage className="inline-flex items-center gap-1">
                  <CircleDotIcon aria-hidden className="size-[1em] shrink-0" />
                  {METHODS[method].title}
                </BreadcrumbPage>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>
        ) : null}
        <DialogTitle className="flex items-center gap-2">
          <TitleIcon aria-hidden className="size-[1em] shrink-0" />
          {current?.title ?? "Add a project"}
        </DialogTitle>
        <DialogDescription asChild>
          <div className="flex min-w-0 flex-wrap items-center gap-1">
            <StepDescription method={method} root={root} failed={candidates.isError} />
          </div>
        </DialogDescription>
      </DialogHeader>

      {method === null ? (
        <>
          <MethodChoices found={list?.length ?? null} onChoose={choose} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
          </DialogFooter>
        </>
      ) : null}

      {method === "create" ? (
        <WizardForm onSubmit={submitCreate}>
          <NameField
            label="Name"
            value={name}
            onChange={setName}
            error={attempted || name !== "" ? projectNameError(name) : null}
            submitError={submitError}
            autoFocus
          />
          <LocationField value={where} onChange={setLocation} attempted={attempted} />
          <DestinationLine location={where} name={name} />
          <StepFooter
            onBack={() => choose(null)}
            pending={pending}
            action="Create"
            pendingAction="Creating…"
          />
        </WizardForm>
      ) : null}

      {method === "clone" ? (
        <WizardForm onSubmit={submitClone}>
          <UrlField
            value={url}
            onChange={setUrl}
            error={attempted || url !== "" ? cloneUrlError(url) : null}
            submitError={submitError}
          />
          <NameField
            label="Folder name"
            value={cloneFolder}
            onChange={setFolder}
            error={attempted || cloneFolder !== "" ? projectNameError(cloneFolder) : null}
            submitError={null}
            hint={folder === null ? "Taken from the URL until you change it." : null}
          />
          <LocationField value={where} onChange={setLocation} attempted={attempted} />
          <DestinationLine location={where} name={cloneFolder} />
          {add.isPending ? (
            <p className="text-content-secondary text-sm" role="status" data-testid="add-project-cloning">
              Cloning on the daemon… you can close this dialog; the project appears when it lands.
            </p>
          ) : null}
          <StepFooter
            onBack={() => choose(null)}
            pending={pending}
            action="Clone"
            pendingAction="Cloning…"
          />
        </WizardForm>
      ) : null}

      {method === "adopt" && outcomes !== null ? (
        <AdoptResults
          outcomes={outcomes}
          running={running}
          onRetry={() => void runAdopt(outcomes.filter((o) => o.message !== null).map((o) => o.path), true)}
          onDone={onClose}
        />
      ) : null}

      {method === "adopt" && outcomes === null ? (
        <WizardForm onSubmit={submitAdopt}>
          <AdoptPicker
            root={root}
            candidates={list}
            loading={candidates.isPending}
            scanError={candidates.isError ? errorMessage(candidates.error) : null}
            onRetryScan={() => void candidates.refetch()}
            filter={filter}
            onFilter={setFilter}
            selected={selected}
            onSelected={(next) => {
              setSelected(next);
              if (next.size > 0) setManualPath("");
            }}
            onClone={() => choose("clone")}
          />
          <ManualPathField
            value={manualPath}
            onChange={(next) => {
              setManualPath(next);
              if (next.trim() !== "") setSelected(new Set());
            }}
            error={attempted || manualPath !== "" ? absolutePathError(manualPath) : null}
          />
          {submitError ? (
            <p role="alert" className="text-destructive text-sm" data-testid="add-project-error">
              {submitError}
            </p>
          ) : null}
          <StepFooter
            onBack={() => choose(null)}
            pending={pending}
            // "Add 0 projects" on a disabled button names an action that cannot
            // happen; until something is chosen the label says what it will do.
            action={
              manualPath.trim() !== "" || adoptPaths.length === 0
                ? "Add project"
                : adoptActionLabel(adoptPaths.length)
            }
            pendingAction="Adding…"
            disabled={adoptPaths.length === 0}
          />
        </WizardForm>
      ) : null}
    </DialogContent>
  );
}

function StepDescription({
  method,
  root,
  failed,
}: {
  method: AddProjectMethod | null;
  root: string | null;
  failed: boolean;
}): React.ReactNode {
  if (method === "create") return "Creates the folder, runs git init and makes one empty commit.";
  if (method === "clone") return "Grove runs git clone on the daemon host, with that host's credentials.";
  if (method === "adopt") return "Choose repositories on the daemon host. Nothing is moved or cloned.";
  if (root !== null) {
    return (
      <>
        New projects go under <LocationLabel path={root} />
      </>
    );
  }
  return failed ? "Grove could not read its projects folder." : "Looking up the projects folder…";
}

/** Step 1: three vendored buttons, each a glyph over a title and its consequence. */
export function MethodChoices({
  found,
  onChoose,
}: {
  found: number | null;
  onChoose: (method: AddProjectMethod) => void;
}): React.ReactNode {
  return (
    <div className="flex flex-col gap-2" role="group" aria-label="How to add the project">
      {METHOD_ORDER.map((method, index) => {
        const { title, consequence, Icon } = METHODS[method];
        const detail = method === "adopt" && found !== null ? `${consequence} (${found} found)` : consequence;
        return (
          <Button
            key={method}
            type="button"
            variant="outline"
            className="h-auto w-full justify-start gap-3 px-3 py-2.5 text-left whitespace-normal"
            onClick={() => onChoose(method)}
            autoFocus={index === 0}
            data-testid={`add-project-method-${method}`}
          >
            <Icon aria-hidden className="size-4 shrink-0" />
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="text-content-primary text-sm font-medium">{title}</span>
              <span className="text-content-tertiary text-xs font-normal">{detail}</span>
            </span>
          </Button>
        );
      })}
    </div>
  );
}

/** A step-2 form: Enter submits the visible step, which is all the keyboard needs. */
function WizardForm({
  onSubmit,
  children,
}: {
  onSubmit: () => Promise<void>;
  children: React.ReactNode;
}): React.ReactNode {
  return (
    <form
      className="flex min-w-0 flex-col gap-4"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void onSubmit();
      }}
    >
      {children}
    </form>
  );
}

function FieldError({ id, message }: { id: string; message: string | null }): React.ReactNode {
  return message ? (
    <p id={id} className="text-destructive text-sm">
      {message}
    </p>
  ) : null;
}

function NameField({
  label,
  value,
  onChange,
  error,
  submitError,
  hint = null,
  autoFocus = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error: string | null;
  submitError: string | null;
  hint?: string | null;
  autoFocus?: boolean;
}): React.ReactNode {
  const id = useId();
  const errorId = useId();
  const message = error ?? submitError;
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={message ? true : undefined}
        aria-describedby={message ? errorId : undefined}
        autoComplete="off"
        spellCheck={false}
        autoFocus={autoFocus}
        data-testid={autoFocus ? "add-project-name" : "add-project-folder"}
      />
      {hint && !message ? <p className="text-content-tertiary text-xs">{hint}</p> : null}
      <FieldError id={errorId} message={message} />
    </div>
  );
}

function UrlField({
  value,
  onChange,
  error,
  submitError,
}: {
  value: string;
  onChange: (value: string) => void;
  error: string | null;
  submitError: string | null;
}): React.ReactNode {
  const id = useId();
  const errorId = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>
        <LinkIcon aria-hidden className="size-[1em] shrink-0" />
        Repository URL
      </Label>
      <Input
        id={id}
        className="font-mono"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="git@git.example.com:acme/widgets.git"
        aria-invalid={error || submitError ? true : undefined}
        aria-describedby={error || submitError ? errorId : undefined}
        autoComplete="off"
        spellCheck={false}
        autoFocus
        data-testid="add-project-url"
      />
      <FieldError id={errorId} message={error} />
      {/* The daemon's own diagnostic (git's redacted tail), kept verbatim and
          preformatted so a multi-line git message keeps its lines. */}
      {submitError && !error ? (
        <p id={errorId} role="alert" className="text-destructive font-mono text-xs whitespace-pre-wrap" data-testid="add-project-error">
          {submitError}
        </p>
      ) : null}
    </div>
  );
}

function LocationField({
  value,
  onChange,
  attempted,
}: {
  value: string;
  onChange: (value: string) => void;
  attempted: boolean;
}): React.ReactNode {
  const id = useId();
  const errorId = useId();
  const error = attempted && value.trim() === "" ? "Enter a parent folder." : null;
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>Location</Label>
      <Input
        id={id}
        className="font-mono"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        autoComplete="off"
        spellCheck={false}
        data-testid="add-project-location"
      />
      <FieldError id={errorId} message={error} />
    </div>
  );
}

/**
 * Where the project will land, typed before anything is written: the parent as
 * a Location and the folder as the Project it is about to become.
 */
function DestinationLine({ location, name }: { location: string; name: string }): React.ReactNode {
  const preview = destinationPreview(location, name);
  if (preview === null) return null;
  return (
    <p
      className="text-content-secondary flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm"
      title={preview}
      data-testid="add-project-destination"
    >
      <span className="text-content-tertiary text-xs">Destination</span>
      <LocationLabel path={location.trim().replace(/\/+$/, "")} />
      <ProjectLabel name={name} />
    </p>
  );
}

function StepFooter({
  onBack,
  pending,
  action,
  pendingAction,
  disabled = false,
}: {
  onBack: () => void;
  pending: boolean;
  action: string;
  pendingAction: string;
  disabled?: boolean;
}): React.ReactNode {
  return (
    <DialogFooter>
      <Button type="button" variant="outline" onClick={onBack} disabled={pending}>
        Back
      </Button>
      <Button type="submit" disabled={pending || disabled} data-testid="add-project-submit">
        {pending ? <LoaderCircleIcon aria-hidden className="size-4 animate-spin" /> : null}
        {pending ? pendingAction : action}
      </Button>
    </DialogFooter>
  );
}

function ManualPathField({
  value,
  onChange,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  error: string | null;
}): React.ReactNode {
  const id = useId();
  const errorId = useId();
  const hintId = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>Or use a repository path instead</Label>
      <Input
        id={id}
        className="font-mono"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="/path/to/repository"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : hintId}
        autoComplete="off"
        spellCheck={false}
        data-testid="add-project-path"
      />
      {error ? (
        <FieldError id={errorId} message={error} />
      ) : (
        <p id={hintId} className="text-content-tertiary text-xs">
          Entering a path clears the table selection.
        </p>
      )}
    </div>
  );
}

/**
 * The existing-repository step: where it looked, a filter, the counts, and a
 * bounded table — or the state that replaces the table, each a different
 * situation with its own way forward.
 */
function AdoptPicker({
  root,
  candidates,
  loading,
  scanError,
  onRetryScan,
  filter,
  onFilter,
  selected,
  onSelected,
  onClone,
}: {
  root: string | null;
  candidates: readonly ProjectCandidateView[] | null;
  loading: boolean;
  scanError: string | null;
  onRetryScan: () => void;
  filter: string;
  onFilter: (value: string) => void;
  selected: ReadonlySet<string>;
  onSelected: (next: ReadonlySet<string>) => void;
  onClone: () => void;
}): React.ReactNode {
  const filterId = useId();
  const filterRef = useRef<HTMLInputElement>(null);
  const all = candidates ?? [];
  const visible = filterCandidates(all, filter);
  const summary = selectionSummary(all, visible, selected);

  let body: React.ReactNode;
  if (loading) {
    body = (
      <p className="text-content-secondary text-sm" role="status" data-testid="add-project-scanning">
        Looking for repositories…
      </p>
    );
  } else if (scanError !== null) {
    body = (
      <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="add-project-scan-error">
        <CircleXIcon aria-hidden className="text-destructive size-4 shrink-0" />
        <span role="alert">Could not scan the projects folder: {scanError}</span>
        <Button type="button" variant="outline" size="sm" onClick={onRetryScan}>
          Retry
        </Button>
      </div>
    );
  } else if (all.length === 0) {
    body = (
      <div className="flex flex-col gap-2 text-sm" data-testid="add-project-no-candidates">
        <p className="flex min-w-0 flex-wrap items-center gap-1">
          No unregistered repositories under {root !== null ? <LocationLabel path={root} /> : "the projects folder"}
        </p>
        <p className="text-content-secondary flex flex-wrap items-center gap-2">
          Use the path field below, or
          <Button type="button" variant="outline" size="sm" onClick={onClone}>
            <GitForkIcon aria-hidden />
            Clone a repository
          </Button>
        </p>
      </div>
    );
  } else {
    body = (
      <>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={filterId}>Filter</Label>
          <Input
            id={filterId}
            ref={filterRef}
            value={filter}
            onChange={(event) => onFilter(event.target.value)}
            placeholder="Search name, path or remote…"
            autoComplete="off"
            spellCheck={false}
            autoFocus
            data-testid="add-project-filter"
          />
        </div>
        <div className="flex min-h-6 flex-wrap items-center justify-between gap-2">
          <p className="text-content-secondary text-sm tabular-nums" data-testid="add-project-summary">
            {summaryLine(summary)}
          </p>
          {summary.selected > 0 ? (
            <Button type="button" variant="ghost" size="xs" onClick={() => onSelected(new Set())}>
              <XIcon aria-hidden />
              Clear selection
            </Button>
          ) : null}
        </div>
        {visible.length === 0 ? (
          <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="add-project-no-matches">
            <span>No repositories match</span>
            <Button type="button" variant="ghost" size="xs" onClick={() => onFilter("")}>
              <XIcon aria-hidden />
              Clear filter
            </Button>
          </div>
        ) : (
          <div
            // `/` jumps to Filter from anywhere in the table, the list idiom
            // every searchable surface in the app already teaches.
            onKeyDown={(event) => {
              if (event.key === "/" && !(event.target instanceof HTMLInputElement && event.target.type !== "checkbox")) {
                event.preventDefault();
                filterRef.current?.focus();
              }
            }}
          >
            <CandidateTable candidates={visible} selected={selected} onSelected={onSelected} />
          </div>
        )}
      </>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {root !== null ? (
        <p className="text-content-secondary flex min-w-0 items-center gap-2 text-sm" data-testid="add-project-look-in">
          <span className="text-content-tertiary text-xs">Look in</span>
          <LocationLabel path={root} />
        </p>
      ) : null}
      {body}
    </div>
  );
}

/** "N matching repositories · M selected · K hidden": every match, not just the rows in view. */
export function summaryLine(summary: { matching: number; selected: number; hidden: number }): string {
  const noun = summary.matching === 1 ? "repository" : "repositories";
  const parts = [`${summary.matching} matching ${noun}`, `${summary.selected} selected`];
  if (summary.hidden > 0) parts.push(`${summary.hidden} hidden`);
  return parts.join(" · ");
}

/**
 * The bounded candidate table: only the rows scroll, the header stays put.
 *
 * Three overrides at the composition seam, none in the vendored files. The
 * bound sits on the radix VIEWPORT, because a `max-h` on the root leaves the
 * viewport's `size-full` resolving against an indefinite height and nothing
 * scrolls. Radix lays its content out as `display: table`, which grows to the
 * longest path and defeats truncation, so that wrapper is put back to `block`.
 * And the vendored `Table` wraps itself in an `overflow-x-auto` div, which
 * would otherwise be the sticky header's scroll container (see
 * `FROZEN_TABLE_HEAD` for the same trap on a native scroller).
 */
export function CandidateTable({
  candidates,
  selected,
  onSelected,
}: {
  candidates: readonly ProjectCandidateView[];
  selected: ReadonlySet<string>;
  onSelected: (next: ReadonlySet<string>) => void;
}): React.ReactNode {
  const toggle = (path: string): void => {
    const next = new Set(selected);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    onSelected(next);
  };
  return (
    <CardShell>
      <ScrollArea
        className={cn(
          "[&_[data-slot=scroll-area-viewport]]:max-h-72",
          "[&_[data-slot=scroll-area-viewport]>div]:block!",
          "[&_[data-slot=table-container]]:overflow-visible",
          "[&_thead]:sticky [&_thead]:top-0 [&_thead]:z-10 [&_thead]:bg-background",
        )}
        data-testid="add-project-candidates"
      >
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className={`w-9 ${HEAD_CELL}`}>
                <span className="sr-only">Select</span>
              </TableHead>
              <TableHead className={HEAD_CELL}>Repository / local path</TableHead>
              <TableHead className={`hidden w-1/3 sm:table-cell ${HEAD_CELL}`}>Remote</TableHead>
              <TableHead className={`w-24 ${HEAD_CELL}`}>Selection</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {candidates.map((candidate) => {
              const on = selected.has(candidate.path);
              return (
                <TableRow
                  key={candidate.path}
                  data-state={on ? "selected" : undefined}
                  onClick={(event) => {
                    // The checkbox toggles itself; a click on it must not toggle twice.
                    if (event.target instanceof HTMLInputElement) return;
                    toggle(candidate.path);
                  }}
                  data-testid="add-project-candidate"
                >
                  <TableCell>
                    <input
                      type="checkbox"
                      className="size-4 align-middle"
                      checked={on}
                      onChange={() => toggle(candidate.path)}
                      aria-label={`Select ${candidate.name}`}
                    />
                  </TableCell>
                  <TableCell className="overflow-hidden">
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <ProjectLabel name={candidate.name} className="max-w-full font-medium" />
                      <LocationLabel path={candidate.path} className="text-content-tertiary max-w-full text-xs" />
                      {/* Narrow layouts stack the remote here rather than scrolling sideways. */}
                      <RemoteMark remote={candidate.remote ?? null} className="sm:hidden" />
                    </div>
                  </TableCell>
                  <TableCell className="hidden overflow-hidden sm:table-cell">
                    <RemoteMark remote={candidate.remote ?? null} />
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1 text-xs">
                      {on ? (
                        <SquareCheckIcon aria-hidden className="size-[1em] shrink-0" />
                      ) : (
                        <SquareIcon aria-hidden className="text-content-tertiary size-[1em] shrink-0" />
                      )}
                      {on ? "Selected" : "—"}
                    </span>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </ScrollArea>
    </CardShell>
  );
}

/** A remote, or its absence drawn as a state with its own shape rather than an empty cell. */
function RemoteMark({ remote, className }: { remote: string | null; className?: string }): React.ReactNode {
  return remote !== null ? (
    // A block flex with a width bound: an `inline-flex` has none, so the inner
    // `truncate` never engaged and the table cell clipped the URL mid-word.
    <span className={cn("flex min-w-0 max-w-full items-center gap-1 text-xs", className)} title={remote}>
      <GitForkIcon aria-hidden className="text-content-tertiary size-[1em] shrink-0" />
      <span className="sr-only">Remote: </span>
      <span className="truncate font-mono">{remote}</span>
    </span>
  ) : (
    <span className={cn("text-content-tertiary inline-flex items-center gap-1 text-xs", className)}>
      <CircleDashedIcon aria-hidden className="size-[1em] shrink-0" />
      No remote
    </span>
  );
}

/** Per-path results after several adds; only the failures can be retried. */
function AdoptResults({
  outcomes,
  running,
  onRetry,
  onDone,
}: {
  outcomes: readonly AddOutcome[];
  running: boolean;
  onRetry: () => void;
  onDone: () => void;
}): React.ReactNode {
  const failed = outcomes.filter((o) => o.message !== null).length;
  const added = outcomes.length - failed;
  return (
    <div className="flex min-w-0 flex-col gap-3" data-testid="add-project-results">
      <p className="text-content-secondary text-sm tabular-nums" role="status">
        {added} added{failed > 0 ? ` · ${failed} failed` : ""}
      </p>
      <ul className="flex min-w-0 flex-col gap-2">
        {outcomes.map((outcome) => (
          <li key={outcome.path} className="flex min-w-0 items-start gap-2 text-sm" data-testid="add-project-result">
            {outcome.message === null ? (
              <CircleCheckIcon aria-hidden className="text-success mt-0.5 size-4 shrink-0" />
            ) : (
              <CircleXIcon aria-hidden className="text-destructive mt-0.5 size-4 shrink-0" />
            )}
            <span className="sr-only">{outcome.message === null ? "Added: " : "Failed: "}</span>
            <span className="flex min-w-0 flex-col gap-0.5">
              <ProjectLabel name={outcome.name} className="max-w-full" />
              <LocationLabel path={outcome.path} className="text-content-tertiary max-w-full text-xs" />
              {outcome.message !== null ? (
                <span className="text-destructive text-xs whitespace-pre-wrap">{outcome.message}</span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
      <DialogFooter>
        {failed > 0 ? (
          <Button type="button" variant="outline" onClick={onRetry} disabled={running}>
            {running ? <LoaderCircleIcon aria-hidden className="size-4 animate-spin" /> : null}
            {failed === 1 ? "Retry the failed project" : `Retry ${failed} failed`}
          </Button>
        ) : null}
        <Button type="button" onClick={onDone} disabled={running} data-testid="add-project-done">
          Done
        </Button>
      </DialogFooter>
    </div>
  );
}
