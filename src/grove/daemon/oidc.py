"""Human sign-in over OIDC: validate an issuer's ID token and name the person behind it.

OIDC is for HUMANS only, so a person can sign in to the dashboard or the CLI
and see sessions. It plays no part in daemon-to-daemon trust, which is the
federation's own mutual key. The issuer is generic (discovery document, JWKS),
so any OIDC provider works and nothing here names one.

The JWKS is fetched lazily and refetched once when a token names a key id the
cached set does not hold, which is how an issuer's key rotation reaches a
long-running daemon without a restart or a timer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from joserfc import jws, jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from grove.core.config import OidcConfig
from grove.core.errors import AuthInvalidToken
from grove.daemon.principal import Principal


@dataclass(slots=True)
class OidcVerifier:
    """Validates ID tokens from one configured issuer."""

    cfg: OidcConfig
    client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=10.0))
    _discovery: dict[str, Any] | None = None
    _keys: KeySet | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def enabled(self) -> bool:
        """Whether an issuer is configured at all."""
        return bool(self.cfg.issuer)

    @staticmethod
    def looks_like_jwt(token: str) -> bool:
        """A compact JWS has exactly three dot-separated parts; a Grove bearer has none."""
        return token.count(".") == 2

    def discovery(self) -> dict[str, Any]:
        """The issuer's discovery document, fetched once."""
        if self._discovery is None:
            url = self.cfg.issuer.rstrip("/") + "/.well-known/openid-configuration"
            response = self.client.get(url)
            response.raise_for_status()
            self._discovery = response.json()
        return self._discovery

    def _key_set(self, *, refresh: bool) -> KeySet:
        with self._lock:
            if self._keys is None or refresh:
                response = self.client.get(self.discovery()["jwks_uri"])
                response.raise_for_status()
                self._keys = KeySet.import_key_set(response.json())
            return self._keys

    def verify(self, token: str) -> Principal:
        """The person an ID token names, or :class:`AuthInvalidToken`.

        Args:
            token: a compact ID token issued by the configured issuer.

        Returns:
            A human :class:`Principal` keyed by ``(iss, sub)``.

        Raises:
            AuthInvalidToken: bad signature, wrong issuer or audience, expired,
                or the issuer could not be reached to check it.
        """
        try:
            # Read, not trusted: the key id only picks which key to verify with.
            kid = jws.extract_compact(token.encode()).headers().get("kid")
        except (JoseError, ValueError) as exc:
            raise AuthInvalidToken("malformed ID token") from exc
        try:
            keys = self._key_set(refresh=False)
            if kid is not None and keys.get_by_kid(kid) is None:
                keys = self._key_set(refresh=True)
            decoded = jwt.decode(token, keys)
            jwt.JWTClaimsRegistry(
                iss={"essential": True, "value": self.cfg.issuer},
                aud={"essential": True, "value": self.cfg.audience or self.cfg.client_id},
                sub={"essential": True},
                exp={"essential": True},
            ).validate(decoded.claims)
        except (JoseError, ValueError, KeyError) as exc:
            raise AuthInvalidToken(f"ID token rejected: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AuthInvalidToken(f"OIDC issuer unreachable: {exc}") from exc
        claims = decoded.claims
        return Principal(
            subject=str(claims["sub"]),
            issuer=str(claims["iss"]),
            kind="human",
            token_id=str(claims.get("jti", "")),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        )


__all__ = ["OidcVerifier"]
