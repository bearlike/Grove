import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NativeInterrupt } from "@/components/grove/workspace/composer";
import { canInterruptNative } from "@/components/grove/workspace/selectors";
import { workspace } from "@/tests/fixtures/fleet";

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  disabled: false,
  pending: false,
  status: "idle",
  native: true,
  onClick: undefined as (() => void) | undefined,
}));
vi.mock("@assistant-ui/react", () => ({
  useAuiState: (selector: (state: unknown) => unknown) => selector({ thread: { isDisabled: mocks.disabled } }),
}));
vi.mock("@/lib/grove/hooks", () => ({
  useWorkspacePeek: () => ({ data: { state: { native: mocks.native, status: mocks.status } } }),
  useInterrupt: () => ({ mutate: mocks.mutate, isPending: mocks.pending }),
}));
vi.mock("@/components/assistant-ui/tooltip-icon-button", () => ({
  TooltipIconButton: ({ children, tooltip, onClick, ...props }: React.PropsWithChildren<{
    tooltip: string; onClick: () => void;
  }>) => {
    mocks.onClick = onClick;
    return <button {...props} title={tooltip}>{children}</button>;
  },
}));
vi.mock("@/components/grove/composer", () => ({ ExpandedComposer: () => null }));
vi.mock("@/components/grove/workspace/thread", () => ({ WorkspaceComposer: () => null }));
vi.mock("@/components/grove/workspace/composer-model", () => ({ ComposerModel: () => null }));

const base = workspace({ id: "interrupt", branch: "main" }).state;

beforeEach(() => {
  mocks.mutate.mockClear();
  mocks.disabled = false;
  mocks.pending = false;
  mocks.native = true;
  mocks.status = "idle";
  mocks.onClick = undefined;
});

describe("native interrupt", () => {
  it.each(["running", "active", "idle"] as const)("remains available for live %s regardless of working inference", (status) => {
    expect(canInterruptNative({ ...base, native: true, status })).toBe(true);
    mocks.status = status;
    const html = renderToStaticMarkup(<NativeInterrupt workspaceId="interrupt" />);
    expect(html).toContain('data-testid="composer-native-interrupt"');
    expect(html).not.toContain("disabled");
    mocks.onClick?.();
    expect(mocks.mutate).toHaveBeenCalledOnce();
  });

  it.each(["offline", "paused", "error", "orphaned", "provisioning"] as const)("does not interrupt %s sessions", (status) => {
    expect(canInterruptNative({ ...base, native: true, status })).toBe(false);
    mocks.status = status;
    expect(renderToStaticMarkup(<NativeInterrupt workspaceId="interrupt" />)).toContain("disabled");
  });

  it("disables the control for a read-only runtime", () => {
    mocks.disabled = true;
    expect(renderToStaticMarkup(<NativeInterrupt workspaceId="interrupt" />)).toContain("disabled");
  });

  it("prevents duplicate pending interruption", () => {
    mocks.pending = true;
    expect(renderToStaticMarkup(<NativeInterrupt workspaceId="interrupt" />)).toContain("disabled");
  });

  it("leaves terminal composer behavior unchanged", () => {
    mocks.native = false;
    expect(renderToStaticMarkup(<NativeInterrupt workspaceId="interrupt" />)).toBe("");
  });
});
