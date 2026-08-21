/**
 * The reader's share passcode: one header name, one place it is kept.
 *
 * A project can require a passcode before its shared links will answer. The
 * daemon checks a hash and never issues a grant, so there is deliberately NO
 * session, NO cookie and NO token exchange for an anonymous reader — the
 * passcode simply rides every request as a header.
 *
 * That choice is what keeps this small. A grant would need a lifetime, a store,
 * a revocation story and a second thing that can be stale; a header needs none
 * of them, and the check stays exactly one hash comparison at the daemon.
 *
 * WHY `sessionStorage` AND NOT `localStorage`: the passcode is somebody else's
 * secret, shared with this reader for as long as they are reading. Session
 * scope means it dies with the tab — a shared machine does not silently keep
 * admitting the next person, and nothing has to remember to clear it. The cost
 * is retyping it in a new tab, which is the correct trade for a credential
 * nobody here owns.
 *
 * Keyed BY TOKEN, so holding the passcode for one project's link never
 * silently authorises another's.
 */
export const SHARE_PASSCODE_HEADER = "x-grove-share-passcode";

const key = (token: string) => `grove-share-passcode:${token}`;

/**
 * The passcode this reader has already supplied for `token`, if any.
 *
 * Defensive on every axis the storage can fail: no `window` (this module is
 * imported by code that also renders on the server), and a disabled or
 * throwing store — private browsing in some browsers — both read as "none
 * supplied", which is a state the caller already handles.
 */
export function readSharePasscode(token: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key(token));
  } catch {
    return null;
  }
}

/** Remember a passcode for this tab, best-effort. */
export function writeSharePasscode(token: string, passcode: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(key(token), passcode);
  } catch {
    // A full or disabled store must not stop somebody reading the page; they
    // will simply be asked again on the next navigation.
  }
}

/**
 * Forget it — used when the daemon refuses a passcode we had stored.
 *
 * Clearing on refusal is what stops a stale value (the project's passcode was
 * changed) from wedging the reader in a silent retry loop against a secret
 * that will never be accepted again.
 */
export function clearSharePasscode(token: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(key(token));
  } catch {
    // Nothing to do; the value simply outlives this attempt.
  }
}
