import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { useState, type ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProvisionLine, ProvisionPanel } from "@/components/workspace/provision-progress";

// The provisioning affordance: the card's one-line readout and the session
// page's banner. Both must show that work is HAPPENING (an indeterminate
// shimmer, never a percentage), how long it has been happening (counted from
// `provision_started_at` on the client's own clock), and the last line the
// provisioner wrote. The panel's fold reveals the real build log.

function Providers({ children }: { children: ReactNode }) {
  const [qc] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const PROGRESS = {
  elapsed_ms: 71_000,
  headline: "#8 [4/9] RUN apt-get install -y build-essential",
  lines: ["#7 resolving image", "#8 [4/9] RUN apt-get install -y build-essential"],
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(PROGRESS), { status: 200 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProvisionLine (grid card)", () => {
  it("renders the elapsed clock off the START STAMP, not the server's elapsed", async () => {
    // The daemon's `elapsed_ms` is stale the instant it leaves the daemon, so a
    // client with a start stamp must count locally — otherwise the number sits
    // frozen between polls and reads as "nothing is happening".
    const now = vi
      .spyOn(Date, "now")
      .mockReturnValue(new Date("2026-08-02T00:02:10Z").getTime());
    render(
      <Providers>
        <ProvisionLine workspaceId="w1" startedAt="2026-08-02T00:00:00Z" />
      </Providers>,
    );
    // 2m 10s from the stamp, NOT the 1m 11s the stubbed daemon response carries.
    expect(screen.getByTestId("provision-elapsed")).toHaveTextContent("2m 10s");
    now.mockRestore();
  });

  it("falls back to the daemon's elapsed_ms when no start stamp was recorded", async () => {
    render(
      <Providers>
        <ProvisionLine workspaceId="w1" startedAt={null} />
      </Providers>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("provision-elapsed")).toHaveTextContent("1m 11s"),
    );
  });

  it("shows the provisioner's last line as the is-it-moving signal", async () => {
    render(
      <Providers>
        <ProvisionLine workspaceId="w1" startedAt={null} />
      </Providers>,
    );
    await waitFor(() => expect(screen.getByText(PROGRESS.headline)).toBeInTheDocument());
  });
});

describe("ProvisionPanel (session page)", () => {
  it("names the state and keeps the log behind a fold", async () => {
    render(
      <Providers>
        <ProvisionPanel workspaceId="w1" startedAt={null} />
      </Providers>,
    );
    expect(screen.getByTestId("provision-panel")).toBeInTheDocument();
    expect(screen.getByTestId("status-badge").dataset.status).toBe("provisioning");
    // Collapsed by default — a build log must not dump into the page uninvited.
    expect(screen.queryByTestId("provision-log")).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("provision-log-toggle"));
    await waitFor(() =>
      expect(screen.getByTestId("provision-log")).toHaveTextContent("#7 resolving image"),
    );
  });

  it("states the expected duration — a silent 6-minute build reads as a hang", async () => {
    render(
      <Providers>
        <ProvisionPanel workspaceId="w1" startedAt={null} />
      </Providers>,
    );
    expect(screen.getByText(/can take several minutes/i)).toBeInTheDocument();
  });
});
