"""Exercise native TLS and credential libraries in the installed Docker runtime."""

from __future__ import annotations

import asyncio
import json
import platform
import sqlite3
import ssl
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.backends.openssl.backend import backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import SecretStr

from paperwrench.auth.credentials import CredentialVault
from paperwrench.config import Settings
from paperwrench.db.models import LocalCredential


def transfer(outgoing: ssl.MemoryBIO, incoming: ssl.MemoryBIO) -> None:
    while data := outgoing.read():
        incoming.write(data)


def probe_tls(directory: Path) -> None:
    """Complete a verified TLS handshake and exchange data without external services."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    certificate_path = directory / "certificate.pem"
    key_path = directory / "key.pem"
    certificate_path.write_bytes(pem)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate_path, key_path)
    client_context = ssl.create_default_context(cadata=pem.decode("ascii"))
    client_in, client_out, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_context.wrap_bio(client_in, client_out, server_hostname="localhost")
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    complete = [False, False]
    for _ in range(100):
        for index, peer in enumerate((client, server)):
            if not complete[index]:
                try:
                    peer.do_handshake()
                    complete[index] = True
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                    pass
        transfer(client_out, server_in)
        transfer(server_out, client_in)
        if all(complete):
            break
    assert all(complete), "Native OpenSSL TLS handshake did not complete"
    assert client_context.verify_mode == ssl.CERT_REQUIRED and client_context.check_hostname
    payload = b"PaperWrench native TLS smoke"
    client.write(payload)
    transfer(client_out, server_in)
    assert server.read(len(payload)) == payload
    server.write(payload)
    transfer(server_out, client_in)
    assert client.read(len(payload)) == payload


async def probe_credentials() -> None:
    settings = Settings(_env_file=None).model_copy(
        update={
            "paperless_url": "http://paperless.invalid",
            "remember_tokens": True,
            "credential_key": SecretStr(Fernet.generate_key().decode("ascii")),
        }
    )
    vault = CredentialVault(settings)
    encrypted = vault.encrypt(42, "native-smoke", "native-smoke-token")
    credential = LocalCredential(
        owner_id=42,
        username="native-smoke",
        paperless_url=settings.paperless_url,
        encrypted_token=encrypted,
    )
    assert vault.decrypt(credential) == "native-smoke-token"
    password_hash = await vault.hash_password("native-smoke-password")
    assert await vault.verify_password(password_hash, "native-smoke-password")
    assert not await vault.verify_password(password_hash, "incorrect-password")


def main() -> None:
    with TemporaryDirectory(prefix="paperwrench-native-") as directory:
        probe_tls(Path(directory))
    asyncio.run(probe_credentials())
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "libc": platform.libc_ver(),
                "sqlite": sqlite3.sqlite_version,
                "openssl": ssl.OPENSSL_VERSION,
                "cryptography_openssl": backend.openssl_version_text(),
                "tls_and_credentials": "ok",
            }
        )
    )


if __name__ == "__main__":
    main()
