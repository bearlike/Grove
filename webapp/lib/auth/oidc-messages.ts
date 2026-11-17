/** Why a sign-in came back to this page, in words; `null` for an unknown or absent code. */
export function signInError(code: string | null): string | undefined {
  switch (code) {
    case "oidc_refused":
      return "Sign-in was refused. Try again, or pair this device instead.";
    case "oidc_expired":
      return "The sign-in took too long. Try again.";
    case "oidc_unreachable":
      return "The identity provider could not be reached.";
    case "oidc_not_configured":
      return "Sign-in with an identity provider is not configured on this Grove.";
    case "daemon_unreachable":
      return "The Grove daemon could not be reached.";
    default:
      return undefined;
  }
}
