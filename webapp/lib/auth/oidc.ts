import * as oauth from "oauth4webapi";

/**
 * Browser sign-in with the organisation's identity provider: auth-code + PKCE.
 *
 * The web server is the OIDC client; the browser only ever sees redirects and
 * the opaque `grove_session` cookie. The ID token this obtains is handed to the
 * daemon (`POST /auth/oidc/session`), which verifies it itself against the
 * issuer's keys and answers with an ordinary Grove bearer. So nothing here is
 * trusted for authorization: a forged token fails at the daemon, and the checks
 * below (state, PKCE, nonce, issuer, audience) exist to stop a login being
 * completed by somebody else's browser, not to grant anything.
 *
 * Configuration is the server's environment, set by `grove web` from the
 * `auth.oidc` block. A confidential client's secret is read from the
 * environment only and never reaches the daemon or the browser.
 */
export interface OidcConfig {
  issuer: URL;
  clientId: string;
  clientSecret: string | null;
}

/** The short-lived, httpOnly cookie that carries one login attempt across the IdP round trip. */
export const OIDC_COOKIE = "grove_oidc";
export const OIDC_COOKIE_MAX_AGE_SECONDS = 600;

export interface PendingLogin {
  verifier: string;
  state: string;
  nonce: string;
  next: string;
}

export function oidcConfig(env: Record<string, string | undefined> = process.env): OidcConfig | null {
  const issuer = env.GROVE_OIDC_ISSUER?.trim();
  const clientId = env.GROVE_OIDC_CLIENT_ID?.trim();
  if (!issuer || !clientId) return null;
  return { issuer: new URL(issuer), clientId, clientSecret: env.GROVE_OIDC_CLIENT_SECRET?.trim() || null };
}

/** Where the IdP sends the browser back to: this server's own callback route. */
export function callbackUrl(origin: string): string {
  return new URL("/api/auth/oidc/callback", origin).toString();
}

/**
 * The origin the BROWSER reached, which is what every redirect must name.
 *
 * `request.nextUrl.origin` is not it: the standalone Next server builds that
 * from its own bind address (`https://0.0.0.0:3000` behind a reverse proxy),
 * so the callback URL sent to the identity provider named a host the provider
 * had never been told about and every login was refused. The proxy states the
 * public host and scheme in `X-Forwarded-Host` / `X-Forwarded-Proto` (the
 * first value of each, as the cookie's `secure` flag already reads); without
 * them, the request's own origin is the public one.
 *
 * Trusting the forwarded host is the same trust `isSecureRequest` extends to
 * the forwarded scheme: this server is only ever reached through its proxy.
 */
export function publicOrigin(headers: Headers, requestOrigin: string): string {
  const host = headers.get("x-forwarded-host")?.split(",")[0]?.trim();
  if (!host) return requestOrigin;
  const proto = headers.get("x-forwarded-proto")?.split(",")[0]?.trim().replace(/:$/, "") || new URL(requestOrigin).protocol.replace(/:$/, "");
  return `${proto}://${host}`;
}

/**
 * The server metadata, with the `iss` response-parameter claim matched to what the
 * redirect actually carries.
 *
 * RFC 9207 lets a provider advertise `authorization_response_iss_parameter_supported`
 * and then put `iss` on every redirect back; the library enforces the advertisement
 * strictly and refuses a redirect without the parameter. A provider that advertises
 * it and does not send it (measured 2026-10-22: every live login refused with
 * `response parameter "iss" (issuer) missing`) therefore locks every user out. The
 * `iss` parameter is a mix-up defence for clients talking to several providers; this
 * client talks to exactly one, and the ID token's `iss` is still verified against the
 * configured issuer by `processAuthorizationCodeResponse`, so dropping the
 * advertisement when the parameter is absent loses nothing this client relied on.
 * When the provider DOES send `iss`, the check stays and the value must match.
 */
export function issParameterAsSent(server: oauth.AuthorizationServer, callback: URL): oauth.AuthorizationServer {
  if (!server.authorization_response_iss_parameter_supported || callback.searchParams.has("iss")) return server;
  return { ...server, authorization_response_iss_parameter_supported: false };
}

/** Only a same-site path survives as a post-login destination, never another origin. */
export function safeNext(candidate: string | null | undefined): string {
  return candidate?.startsWith("/") && !candidate.startsWith("//") ? candidate : "/";
}

export class OidcClient {
  private constructor(
    private readonly config: OidcConfig,
    private readonly server: oauth.AuthorizationServer,
    private readonly client: oauth.Client,
    private readonly auth: oauth.ClientAuth,
  ) {}

  /** Discovery is one GET per call; a login is rare enough that caching it buys nothing. */
  static async discover(config: OidcConfig): Promise<OidcClient> {
    const response = await oauth.discoveryRequest(config.issuer, { algorithm: "oidc" });
    const server = await oauth.processDiscoveryResponse(config.issuer, response);
    const auth = config.clientSecret ? oauth.ClientSecretPost(config.clientSecret) : oauth.None();
    return new OidcClient(config, server, { client_id: config.clientId }, auth);
  }

  /** A fresh attempt: the IdP URL to send the browser to, and what to remember until it returns. */
  async begin(redirectUri: string, next: string): Promise<{ url: URL; pending: PendingLogin }> {
    if (!this.server.authorization_endpoint) throw new Error("issuer publishes no authorization endpoint");
    const verifier = oauth.generateRandomCodeVerifier();
    const pending: PendingLogin = {
      verifier,
      state: oauth.generateRandomState(),
      nonce: oauth.generateRandomNonce(),
      next: safeNext(next),
    };
    const url = new URL(this.server.authorization_endpoint);
    url.searchParams.set("client_id", this.config.clientId);
    url.searchParams.set("redirect_uri", redirectUri);
    url.searchParams.set("response_type", "code");
    url.searchParams.set("scope", "openid");
    url.searchParams.set("code_challenge", await oauth.calculatePKCECodeChallenge(verifier));
    url.searchParams.set("code_challenge_method", "S256");
    url.searchParams.set("state", pending.state);
    url.searchParams.set("nonce", pending.nonce);
    return { url, pending };
  }

  /** The IdP's redirect back, checked against the attempt it belongs to; returns the ID token. */
  async complete(callback: URL, redirectUri: string, pending: PendingLogin): Promise<string> {
    const params = oauth.validateAuthResponse(
      issParameterAsSent(this.server, callback),
      this.client,
      callback,
      pending.state,
    );
    const response = await oauth.authorizationCodeGrantRequest(
      this.server,
      this.client,
      this.auth,
      params,
      redirectUri,
      pending.verifier,
    );
    const result = await oauth.processAuthorizationCodeResponse(this.server, this.client, response, {
      expectedNonce: pending.nonce,
      requireIdToken: true,
    });
    if (!result.id_token) throw new Error("the identity provider returned no ID token");
    return result.id_token;
  }
}
