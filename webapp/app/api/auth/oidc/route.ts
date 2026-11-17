import { NextResponse } from "next/server";
import { oidcConfig } from "@/lib/auth/oidc";

/**
 * Whether the login page should offer identity-provider sign-in, and as whom.
 *
 * Unauthenticated by design (the login page asks before anyone has a session),
 * so it carries only what the IdP redirect would reveal anyway: the issuer's
 * host. The client id and any secret stay on this server.
 */
export function GET(): NextResponse {
  const config = oidcConfig();
  return NextResponse.json(config ? { enabled: true, issuer: config.issuer.host } : { enabled: false });
}
