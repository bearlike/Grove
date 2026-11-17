import { NextRequest, NextResponse } from "next/server";
import { isSecureRequest } from "@/lib/auth/cookie-store";
import {
  OIDC_COOKIE,
  OIDC_COOKIE_MAX_AGE_SECONDS,
  OidcClient,
  callbackUrl,
  oidcConfig,
  publicOrigin,
  safeNext,
} from "@/lib/auth/oidc";

/** Begin a sign-in: remember this attempt in a short-lived cookie, then send the browser to the IdP. */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const config = oidcConfig();
  if (!config) return loginWith(request, "oidc_not_configured");
  let started: Awaited<ReturnType<OidcClient["begin"]>>;
  try {
    const client = await OidcClient.discover(config);
    started = await client.begin(
      callbackUrl(publicOrigin(request.headers, request.nextUrl.origin)),
      safeNext(request.nextUrl.searchParams.get("next")),
    );
  } catch {
    return loginWith(request, "oidc_unreachable");
  }
  const response = NextResponse.redirect(started.url);
  response.cookies.set({
    name: OIDC_COOKIE,
    value: Buffer.from(JSON.stringify(started.pending)).toString("base64url"),
    httpOnly: true,
    // `lax`, not `strict`: the IdP's redirect back is a top-level cross-site
    // navigation, and a strict cookie would not ride it.
    sameSite: "lax",
    secure: isSecureRequest(request.headers.get("x-forwarded-proto"), request.nextUrl.protocol),
    path: "/api/auth/oidc",
    maxAge: OIDC_COOKIE_MAX_AGE_SECONDS,
  });
  return response;
}

function loginWith(request: NextRequest, error: string): NextResponse {
  const url = new URL("/login", publicOrigin(request.headers, request.nextUrl.origin));
  url.searchParams.set("error", error);
  return NextResponse.redirect(url);
}
