#!/usr/bin/env python3
"""Seed the dedicated development Paperless-ngx instance.

Run against the DISPOSABLE dev stack only (docker-compose.dev.yml). The script
refuses to run against anything that looks like a populated real library.

What it creates, and why:

* Document type ``Releve de vacations`` (stored with its real accents:
  "Releve" is written here only to keep this docstring ASCII) - the type used
  by the reference bulk-rename acceptance scenario.

* Custom fields ``Periode concernee`` (string) and ``Montant`` (monetary,
  EUR) - the two fields the rename scenario reads.

* SEVERAL ADDITIONAL custom fields (``Reference interne``, ``Etablissement``,
  ``Valide``, ``Date de reglement``, ``Commentaire``) populated on the same
  documents. These are not decorative: they are the regression surface for the
  single most dangerous Paperless behaviour PaperWrench has to work around.

  Paperless-ngx serialises documents with ``drf_writable_nested``'s
  ``NestedUpdateMixin``. A PATCH carrying ``custom_fields`` REPLACES the whole
  collection: every CustomFieldInstance omitted from the payload is DELETED.
  A naive "just set Montant" implementation therefore silently destroys the
  five other fields. Seeding several fields per document means the
  custom-field preservation test fails loudly if that regression ever
  reappears.

Field names and values are deliberately French, with accents, spaces, euro
amounts and dd/mm/yyyy dates, so encoding bugs surface in development rather
than on a real library.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx

# Names carry accents on purpose: they exercise UTF-8 end to end.
DOCUMENT_TYPE = "Relev\u00e9 de vacations"
FIELD_PERIODE = "P\u00e9riode concern\u00e9e"
FIELD_MONTANT = "Montant"

# The "collateral damage" fields. Their survival is the assertion.
COLLATERAL_FIELDS: list[tuple[str, str]] = [
    ("R\u00e9f\u00e9rence interne", "string"),
    ("\u00c9tablissement", "string"),
    ("Valid\u00e9", "boolean"),
    ("Date de r\u00e8glement", "date"),
    ("Commentaire", "string"),
]

MONTHS = [
    "janvier",
    "f\u00e9vrier",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "ao\u00fbt",
    "septembre",
    "octobre",
    "novembre",
    "d\u00e9cembre",
]

ETABLISSEMENTS = [
    "CH Valenciennes",
    "Clinique Saint-Roch",
    "H\u00f4pital Priv\u00e9 de Villeneuve",
]

# Refuse to touch anything that is not obviously a throwaway instance.
#
# These are matched as EXACT hostnames, never as substrings: a substring test
# would happily accept "paperless.my-real-domain.example" and seed - then let
# later tests write to - somebody's actual library.
SAFE_HOSTNAMES = frozenset(
    {
        "paperless",  # the dev-stack service name
        "localhost",
        "127.0.0.1",
        "::1",
        "host.docker.internal",
    }
)
MAX_EXISTING_DOCUMENTS = 200


class SeedError(RuntimeError):
    pass


def api_version() -> str:
    return os.environ.get("PAPERLESS_API_VERSION", "10")


def assert_dev_sandbox(url: str) -> None:
    """Refuse any host that is not an exact match for the dev sandbox."""
    hostname = (urlsplit(url).hostname or "").lower()
    if hostname not in SAFE_HOSTNAMES:
        raise SeedError(
            f"Refusing to seed {url!r}: host {hostname!r} is not the dev sandbox. "
            "Allowed hosts: " + ", ".join(sorted(SAFE_HOSTNAMES)) + ". "
            "This script must never run against a real Paperless-ngx library."
        )


def build_client() -> httpx.Client:
    url = os.environ.get("PAPERLESS_URL", "http://localhost:8010").rstrip("/")
    assert_dev_sandbox(url)

    username = os.environ.get("PAPERLESS_USERNAME", "admin")
    password = os.environ.get("PAPERLESS_PASSWORD", "admin")

    return httpx.Client(
        base_url=url,
        auth=(username, password),
        headers={"Accept": f"application/json; version={api_version()}"},
        timeout=30.0,
        follow_redirects=True,
    )


def guard_not_a_real_library(client: httpx.Client) -> None:
    """Second safety net: a populated instance is almost certainly not the sandbox."""
    response = client.get("/api/documents/", params={"page_size": 1})
    response.raise_for_status()
    count = int(response.json().get("count", 0))
    if count > MAX_EXISTING_DOCUMENTS:
        raise SeedError(
            f"Refusing to seed: the instance already holds {count} documents. "
            "The dev sandbox is expected to be nearly empty."
        )


def check_api_version(response: httpx.Response) -> None:
    reported = response.headers.get("x-api-version")
    if reported and reported != api_version():
        print(
            f"  ! server negotiated API version {reported}, expected {api_version()}",
            file=sys.stderr,
        )


def get_or_create(
    client: httpx.Client, endpoint: str, name: str, payload: dict[str, Any]
) -> int:
    """Idempotent create: reseeding must not duplicate anything."""
    existing = client.get(endpoint, params={"page_size": 100})
    existing.raise_for_status()
    check_api_version(existing)
    for item in existing.json().get("results", []):
        if item.get("name") == name:
            print(f"  = {endpoint}{name!r} already exists (id={item['id']})")
            return int(item["id"])

    created = client.post(endpoint, json=payload)
    if created.status_code >= 400:
        raise SeedError(f"POST {endpoint} failed: {created.status_code} {created.text}")
    item_id = int(created.json()["id"])
    print(f"  + {endpoint}{name!r} created (id={item_id})")
    return item_id


def seed_custom_fields(client: httpx.Client) -> dict[str, int]:
    print("Custom fields")
    fields: dict[str, int] = {}

    fields[FIELD_PERIODE] = get_or_create(
        client,
        "/api/custom_fields/",
        FIELD_PERIODE,
        {"name": FIELD_PERIODE, "data_type": "string"},
    )
    # Monetary values are stored as an ISO-4217 prefixed string ("EUR123.45").
    fields[FIELD_MONTANT] = get_or_create(
        client,
        "/api/custom_fields/",
        FIELD_MONTANT,
        {"name": FIELD_MONTANT, "data_type": "monetary", "extra_data": {"default_currency": "EUR"}},
    )
    for name, data_type in COLLATERAL_FIELDS:
        fields[name] = get_or_create(
            client,
            "/api/custom_fields/",
            name,
            {"name": name, "data_type": data_type},
        )
    return fields


def upload_document(client: httpx.Client, title: str, body: str, doc_type_id: int) -> None:
    """Upload a tiny text document. Consumption is asynchronous in Paperless."""
    files = {"document": (f"{title}.txt", body.encode("utf-8"), "text/plain")}
    data = {"title": title, "document_type": str(doc_type_id)}
    response = client.post("/api/documents/post_document/", files=files, data=data)
    if response.status_code >= 400:
        raise SeedError(f"upload failed for {title!r}: {response.status_code} {response.text}")


def main() -> int:
    count = int(os.environ.get("SEED_DOCUMENT_COUNT", "24"))

    with build_client() as client:
        guard_not_a_real_library(client)

        print("Document type")
        doc_type_id = get_or_create(
            client,
            "/api/document_types/",
            DOCUMENT_TYPE,
            {"name": DOCUMENT_TYPE, "matching_algorithm": 0},
        )

        fields = seed_custom_fields(client)

        print(f"Documents ({count})")
        for index in range(count):
            month = MONTHS[index % 12]
            year = 2024 + (index // 12)
            etablissement = ETABLISSEMENTS[index % len(ETABLISSEMENTS)]
            amount = 120.50 + index * 37.25
            settled = date(year, (index % 12) + 1, min(28, index + 1))

            # Deliberately inconsistent titles: this is the mess the reference
            # bulk-rename scenario has to clean up.
            title = f"scan_{year}{index + 1:02d}_vacations_{index:03d}"
            body = (
                f"{DOCUMENT_TYPE}\n"
                f"{FIELD_PERIODE}: {month} {year}\n"
                f"{FIELD_MONTANT}: {amount:.2f} EUR\n"
                f"\u00c9tablissement: {etablissement}\n"
                f"Date de r\u00e8glement: {settled.strftime('%d/%m/%Y')}\n"
            )
            upload_document(client, title, body, doc_type_id)
            print(f"  + queued {title!r}")

        print()
        print("Uploaded. Paperless consumes documents asynchronously; wait for the")
        print("task queue to drain, then run scripts/seed_dev_custom_field_values.py")
        print("to populate the custom fields on the consumed documents.")
        print()
        print("Custom field ids:")
        for name, field_id in fields.items():
            print(f"  {field_id:>3}  {name}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
