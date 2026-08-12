"use client";

import { useEffect, useState } from "react";

/**
 * SSR-safe `min-width` match. Starts false so the server render and the first
 * client render agree; jsdom's `matchMedia` stub also reports false, which puts
 * component tests in the narrow band deliberately.
 */
export function useMinWidth(px: number): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    const query = window.matchMedia(`(min-width:${px}px)`);
    const sync = (): void => setMatches(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, [px]);

  return matches;
}
