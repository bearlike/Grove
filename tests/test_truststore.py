"""The process-wide native TLS integration degrades rather than aborting Grove."""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

import pytest
import truststore
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from grove._truststore import CA_PATH_ENV, DISABLE_ENV, TrustStoreError, use_system_trust_store


def test_the_system_trust_store_is_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DISABLE_ENV, raising=False)
    calls: list[None] = []

    def inject() -> None:
        calls.append(None)

    # Replacing the public injector verifies our call without changing ssl's
    # global state, which the real injection intentionally rewrites.
    monkeypatch.setattr(truststore, "inject_into_ssl", inject)

    assert use_system_trust_store() is None
    assert calls == [None]


def test_an_unreadable_system_trust_store_returns_a_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DISABLE_ENV, raising=False)

    def inject() -> None:
        raise RuntimeError("unavailable")

    monkeypatch.setattr(truststore, "inject_into_ssl", inject)

    assert use_system_trust_store() == "could not read the system trust store: unavailable"


def test_native_tls_opt_out_skips_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLE_ENV, " false ")

    def must_not_inject() -> None:
        raise AssertionError("opt-out must skip the injector")

    monkeypatch.setattr(truststore, "inject_into_ssl", must_not_inject)

    assert use_system_trust_store() == f"disabled by {DISABLE_ENV}"


def _write_ca_and_server_certificate(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Grove test CA {tmp_path.name}")])
    now = datetime.datetime.now(datetime.UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    ca_path = tmp_path / "private-ca.pem"
    cert_path = tmp_path / "server.pem"
    key_path = tmp_path / "server-key.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


def test_additional_ca_is_loaded_by_a_later_httpx_process(tmp_path: Path) -> None:
    """The injected context is created by HTTPX after setup, not passed to it.

    One child process and one default HTTPX client verify a private-style chain
    and a separately-signed public-style chain. The latter is installed as the
    certifi input, so this proves the configured CA is ADDITIVE to the bundle
    HTTPX normally supplies without making the test reach the network.
    """
    ca_path, cert_path, key_path = _write_ca_and_server_certificate(tmp_path)
    public_ca_path, public_cert_path, public_key_path = _write_ca_and_server_certificate(
        tmp_path / "public"
    )
    script = f"""
import http.server
import os
import ssl
import threading
import httpx
from grove._truststore import use_system_trust_store

os.environ["SSL_CERT_FILE"] = {str(public_ca_path)!r}


def serve(cert, key):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

private = serve({str(cert_path)!r}, {str(key_path)!r})
public = serve({str(public_cert_path)!r}, {str(public_key_path)!r})
try:
    use_system_trust_store({str(ca_path)!r})
    with httpx.Client() as client:
        assert client.get(f"https://localhost:{{private.server_port}}").status_code == 200
        assert client.get(f"https://localhost:{{public.server_port}}").status_code == 200
finally:
    private.shutdown()
    public.shutdown()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_missing_additional_ca_path_fails_loudly(tmp_path: Path) -> None:
    missing = tmp_path / "missing-ca.pem"
    with pytest.raises(TrustStoreError, match=CA_PATH_ENV):
        use_system_trust_store(missing)


def test_unreadable_additional_ca_path_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ca.pem"
    path.write_text("certificate", encoding="utf-8")

    def unreadable(_self: Path, *_args: object, **_kwargs: object) -> object:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", unreadable)
    with pytest.raises(TrustStoreError, match="unreadable"):
        use_system_trust_store(path)
