#!/usr/bin/env python3
"""Populate custom field values on the seeded development documents.

Runs as a second pass because Paperless consumes uploads asynchronously: the
documents created by ``seed_dev_paperless.py`` only exist once the task queue
has drained.

This script is also the reference implementation of the read-modify-write
pattern that PaperWrench itself must use everywhere (see ADR-0004): it GETs the
document, merges into the existing ``custom_fields`` list, and PATCHes the FULL
list back. Sending only the field being changed would delete all the others,
because Paperless-ngx serialises documents through
``drf_writable_nested.NestedUpdateMixin``.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date
from typing import Any

import httpx

from seed_dev_paperless import COLLATERAL_FIELDS
from seed_dev_paperless import DOCUMENT_TYPE
from seed_dev_paperless import ETABLISSEMENTS
from seed_dev_paperless import FIELD_MONTANT
from seed_dev_paperless import FIELD_PERIODE
from seed_dev_paperless import MONTHS
from seed_dev_paperless import SeedError
from seed_dev_paperless import build_client

TITLE_PATTERN = re.compile(r"^scan_(\d{4})(\d{2})_vacations_(\d{3})$")


def fetch_all(client: httpx.Client, endpoint: str, **params: Any) -> list[dict[str, Any]]:
    """Follow Paperless pagination to the end."""
    items: list[dict[str, Any]] = []
    query: dict[str, Any] = {"page_size": 100, **params}
    url: str | None = endpoint
    while url:
        response = client.get(url, params=query if url == endpoint else None)
        response.raise_for_status()
        payload = response.json()
        items.extend(payload.get("results", []))
        url = payload.get("next")
    return items


def field_ids(client: httpx.Client) -> dict[str, int]:
    return {
        field["name"]: int(field["id"])
        for field in fetch_all(client, "/api/custom_fields/")
    }


def document_type_id(client: httpx.Client) -> int:
    for item in fetch_all(client, "/api/document_types/"):
        if item["name"] == DOCUMENT_TYPE:
            return int(item["id"])
    raise SeedError(
        f"Document type {DOCUMENT_TYPE!r} not found. Run seed_dev_paperless.py first."
    )


def build_values(index: int, year: int, ids: dict[str, int]) -> list[dict[str, Any]]:
    month = MONTHS[index % 12]
    amount = 120.50 + index * 37.25
    settled = date(year, (index % 12) + 1, min(28, index + 1))

    values: list[dict[str, Any]] = [
        {"field": ids[FIELD_PERIODE], "value": f"{month} {year}"},
        # Monetary custom fields store an ISO-4217 prefix, not a bare number.
        {"field": ids[FIELD_MONTANT], "value": f"EUR{amount:.2f}"},
        {"field": ids["R\u00e9f\u00e9rence interne"], "value": f"REF-{year}-{index:04d}"},
        {
            "field": ids["\u00c9tablissement"],
            "value": ETABLISSEMENTS[index % len(ETABLISSEMENTS)],
        },
        {"field": ids["Valid\u00e9"], "value": index % 3 != 0},
        {"field": ids["Date de r\u00e8glement"], "value": settled.isoformat()},
    ]
    # Leave some documents without a comment so "empty vs missing" is testable.
    if index % 4 != 0:
        values.append(
            {
                "field": ids["Commentaire"],
                "value": f"Vacations de {month} \u2014 contr\u00f4l\u00e9 le {settled:%d/%m/%Y}",
            }
        )
    return values


def merge_custom_fields(
    existing: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge by field id, preserving every field not being updated.

    This is the whole point: the returned list is the COMPLETE desired state,
    because Paperless replaces the collection wholesale.
    """
    merged = {int(item["field"]): dict(item) for item in existing}
    for update in updates:
        merged[int(update["field"])] = dict(update)
    return list(merged.values())


def main() -> int:
    dry_run = os.environ.get("SEED_DRY_RUN", "").lower() in {"1", "true", "yes"}

    with build_client() as client:
        ids = field_ids(client)
        missing = [
            name
            for name in [FIELD_PERIODE, FIELD_MONTANT, *(n for n, _ in COLLATERAL_FIELDS)]
            if name not in ids
        ]
        if missing:
            raise SeedError(f"missing custom fields: {missing}. Run seed_dev_paperless.py first.")

        type_id = document_type_id(client)
        documents = fetch_all(client, "/api/documents/", document_type__id=type_id)
        if not documents:
            raise SeedError(
                "No seeded documents found yet. Paperless consumes uploads "
                "asynchronously; wait for the task queue to drain and retry."
            )

        print(f"{len(documents)} document(s) of type {DOCUMENT_TYPE!r}")
        updated = 0
        for document in documents:
            match = TITLE_PATTERN.match(str(document.get("title", "")))
            if not match:
                print(f"  - skipping {document.get('title')!r} (not a seeded title)")
                continue

            year = int(match.group(1))
            index = int(match.group(3))

            # Read-modify-write: fetch the current state, merge, send it whole.
            current = client.get(f"/api/documents/{document['id']}/")
            current.raise_for_status()
            existing = current.json().get("custom_fields", []) or []
            payload = merge_custom_fields(existing, build_values(index, year, ids))

            if dry_run:
                print(f"  ~ would set {len(payload)} field(s) on #{document['id']}")
                continue

            response = client.patch(
                f"/api/documents/{document['id']}/", json={"custom_fields": payload}
            )
            if response.status_code >= 400:
                raise SeedError(
                    f"PATCH #{document['id']} failed: {response.status_code} {response.text}"
                )
            updated += 1
            print(f"  + #{document['id']} {document['title']!r}: {len(payload)} field(s)")

        print()
        print(f"Done. {updated} document(s) updated.")
        print(
            "Every seeded document now carries several custom fields, so any "
            "regression in custom-field preservation is immediately visible."
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
