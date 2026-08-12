import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UserMessageResizeObserverRegistry } from "@/components/grove/workspace/user-message";

/**
 * Pins the registry that replaced one `ResizeObserver` per user-message
 * bubble (134 measured on a single transcript mount) with one shared
 * instance. These cases exercise the registry directly rather than mounting
 * `useUserMessageCollapse` — this repo's vitest runs `environment: "node"`
 * (jsdom does not load on this host), so there is no layout engine to mount
 * a hook against; a fake `ResizeObserver` stubbed onto `globalThis` is the
 * only way to see the dispatch behave.
 */

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];
  readonly observed = new Set<Element>();
  private readonly callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    FakeResizeObserver.instances.push(this);
  }

  observe(element: Element): void {
    this.observed.add(element);
  }

  unobserve(element: Element): void {
    this.observed.delete(element);
  }

  disconnect(): void {
    this.observed.clear();
  }

  /** Simulate the browser firing the observer's single native callback. */
  fire(targets: Element[]): void {
    const entries = targets.map((target) => ({ target }) as ResizeObserverEntry);
    this.callback(entries, this as unknown as ResizeObserver);
  }
}

const elementA = {} as Element;
const elementB = {} as Element;

describe("UserMessageResizeObserverRegistry", () => {
  beforeEach(() => {
    FakeResizeObserver.instances = [];
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shares ONE observer instance across two elements", () => {
    const registry = new UserMessageResizeObserverRegistry();
    registry.observe(elementA, vi.fn());
    registry.observe(elementB, vi.fn());

    expect(FakeResizeObserver.instances).toHaveLength(1);
    const [observer] = FakeResizeObserver.instances;
    expect(observer!.observed.has(elementA)).toBe(true);
    expect(observer!.observed.has(elementB)).toBe(true);
  });

  it("routes each dispatch to the callback registered for that element", () => {
    const registry = new UserMessageResizeObserverRegistry();
    const callbackA = vi.fn();
    const callbackB = vi.fn();
    registry.observe(elementA, callbackA);
    registry.observe(elementB, callbackB);

    const [observer] = FakeResizeObserver.instances;
    observer!.fire([elementA]);

    expect(callbackA).toHaveBeenCalledOnce();
    expect(callbackB).not.toHaveBeenCalled();
  });

  it("disposing one element leaves the other observed and reachable", () => {
    const registry = new UserMessageResizeObserverRegistry();
    const callbackA = vi.fn();
    const callbackB = vi.fn();
    const disposeA = registry.observe(elementA, callbackA);
    registry.observe(elementB, callbackB);

    disposeA();

    const [observer] = FakeResizeObserver.instances;
    expect(observer!.observed.has(elementA)).toBe(false);
    expect(observer!.observed.has(elementB)).toBe(true);

    // The shared observer is never torn down by a disposal — only a real
    // native ResizeObserver fires against removed targets, but the stand-in
    // for that guarantee is that no second instance was constructed and
    // a routed callback for a disposed element is a no-op rather than a throw.
    observer!.fire([elementA, elementB]);
    expect(callbackA).not.toHaveBeenCalled();
    expect(callbackB).toHaveBeenCalledOnce();
    expect(FakeResizeObserver.instances).toHaveLength(1);
  });

  it("falls back to a no-op disposer when ResizeObserver is undefined (SSR)", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    const registry = new UserMessageResizeObserverRegistry();
    const dispose = registry.observe(elementA, vi.fn());

    expect(() => dispose()).not.toThrow();
    expect(FakeResizeObserver.instances).toHaveLength(0);
  });
});
