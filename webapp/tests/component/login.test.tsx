import { describe, expect, test, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => new URLSearchParams(""),
}));

import LoginPage from "@/app/login/page";

beforeEach(() => {
  pushMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("LoginPage", () => {
  test("idle phase prefills a label and shows a Pair button", () => {
    render(<LoginPage />);
    expect(
      screen.getByRole("button", { name: /pair this device/i }),
    ).toBeInTheDocument();
    const input = screen.getByPlaceholderText(/iPhone, Office Mac/i) as HTMLInputElement;
    expect(input.value.length).toBeGreaterThan(0);
  });

  test("clicking Pair POSTs to /api/auth/pair and shows the code", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          challenge_id: "11111111-1111-1111-1111-111111111111",
          code: "BEAR-7421",
          state: "pending",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<LoginPage />);
    fireEvent.click(screen.getByRole("button", { name: /pair this device/i }));

    expect(await screen.findByText("BEAR-7421")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/pair",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
  });

  test("on consume the page redirects to next= path", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            challenge_id: "11111111-1111-1111-1111-111111111111",
            code: "ABCD-1234",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            challenge_id: "11111111-1111-1111-1111-111111111111",
            state: "consumed",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<LoginPage />);
    fireEvent.click(screen.getByRole("button", { name: /pair this device/i }));

    // Allow the pair POST microtask to settle.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    // Trigger the first poll (interval is 2000ms).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100);
    });
    // Allow the redirect setTimeout to settle.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });

    expect(pushMock).toHaveBeenCalledWith("/");
  });

  test("daemon error surfaces as inline alert and offers Try again", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ detail: { error: "rate_limited", message: "slow down" } }),
        { status: 429, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<LoginPage />);
    fireEvent.click(screen.getByRole("button", { name: /pair this device/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/slow down/i);
    });
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});
