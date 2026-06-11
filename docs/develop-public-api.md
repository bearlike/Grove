# Public API

The `grove.core` package re-exports its contract from one file. Internal
modules (`config`, `git`, `tmux`, `store`, `manager`, `workspace`,
`activity`, `sessions`, `registry`) are renamable; clients import from
the package root. The names below are what `from grove.core import X` is
allowed to reach for.

The list below is grouped by concern. Where a docstring is missing, the
rendered page surfaces the gap. That is the right pressure to write one,
not to paper over it here.

## Configuration

::: grove.core.GroveConfig
::: grove.core.AgentSpec
::: grove.core.load_config

## Lifecycle

::: grove.core.WorkspaceManager
::: grove.core.WorkspaceEvent
::: grove.core.build

## Multi-repo and activity

::: grove.core.RepoRegistry
::: grove.core.ActivityService
::: grove.core.DashboardSnapshot

## Sessions

::: grove.core.SessionExplorer
::: grove.core.SessionListing

## Workspace state

::: grove.core.WorkspaceState
::: grove.core.WorkspaceStatus
::: grove.core.WorkspacePeek
::: grove.core.Placement
::: grove.core.BranchProvenance
::: grove.core.InitStatus
::: grove.core.CommitSummary

## Branch-source contracts

::: grove.core.BranchPlan
::: grove.core.AutoBranch
::: grove.core.NewNamedBranch
::: grove.core.ExistingLocalBranch
::: grove.core.TrackRemoteBranch
::: grove.core.RootBranch
::: grove.core.BranchInfo
::: grove.core.CreateWorkspaceRequest
::: grove.core.UpdateWorkspaceRequest

## Wire views

::: grove.core.WorkspaceStateView
::: grove.core.WorkspacePeekView
::: grove.core.CommitSummaryView
::: grove.core.AttachInstructionView

## Errors

::: grove.core.GroveError
::: grove.core.BranchError
::: grove.core.BranchConflict
::: grove.core.BranchAlreadyCheckedOut
::: grove.core.BranchNotFound

## Attach plumbing

::: grove.core.AttachInstruction
