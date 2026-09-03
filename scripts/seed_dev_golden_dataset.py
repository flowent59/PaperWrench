#!/usr/bin/env python3
"""Seed the GOLDEN DATASET on the development Paperless instance.

This is not a realistic sample of a document library, and it is not trying to
be. It is a collection of *deliberately imperfect* cases, chosen so that the
transformation, filtering and data-quality engines meet their awkward inputs
in development rather than on somebody's real archive.

The dataset intentionally contains:

* titles that are ALREADY CORRECT   - a rename must be a no-op, not a rewrite
* titles that are WRONG             - the normal case to fix
* titles that are INCONSISTENT      - several competing conventions at once
* ``Periode concernee`` PRESENT and ABSENT
* ``Montant`` PRESENT, ABSENT and EQUAL TO ZERO
* several other custom fields populated, so that any regression in
  custom-field preservation destroys something visible
* French accents everywhere, and decimal monetary values

The zero amount matters more than it looks: ``EUR0.00`` is falsy in almost
every language a template might be evaluated in, so "amount is missing" and
"amount is zero" are constantly conflated. Here they are distinct rows.

Safety: reuses the strict exact-hostname guard from seed_dev_paperless.py.
This script must never run against a real library.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

import httpx

from seed_dev_paperless import DOCUMENT_TYPE
from seed_dev_paperless import FIELD_MONTANT
from seed_dev_paperless import FIELD_PERIODE
from seed_dev_paperless import SeedError
from seed_dev_paperless import build_client
from seed_dev_paperless import get_or_create
from seed_dev_paperless import guard_not_a_real_library
from seed_dev_paperless import seed_custom_fields

FIELD_REFERENCE = "R\u00e9f\u00e9rence interne"
FIELD_ETABLISSEMENT = "\u00c9tablissement"
FIELD_VALIDE = "Valid\u00e9"
FIELD_REGLEMENT = "Date de r\u00e8glement"
FIELD_COMMENTAIRE = "Commentaire"

# The convention the rename scenario is supposed to converge on.
CANONICAL = "{periode} - Relev\u00e9 de vacations"


@dataclass(frozen=True)
class Case:
    """One deliberately-shaped document."""

    key: str
    title: str
    #: Why this row exists. Ends up in the Commentaire field for traceability.
    intent: str
    values: dict[str, Any] = dataclass_field(default_factory=dict)


def _canonical(periode: str) -> str:
    return CANONICAL.format(periode=periode)


CASES: list[Case] = [
    # -- titles that are ALREADY CORRECT -----------------------------------
    Case(
        key="ok-01",
        title=_canonical("janvier 2024"),
        intent="titre deja correct - un rename doit etre un no-op",
        values={
            FIELD_PERIODE: "janvier 2024",
            FIELD_MONTANT: "EUR1250.00",
            FIELD_ETABLISSEMENT: "CH Valenciennes",
            FIELD_VALIDE: True,
            FIELD_REGLEMENT: "2024-02-05",
        },
    ),
    Case(
        key="ok-02",
        title=_canonical("f\u00e9vrier 2024"),
        intent="titre deja correct avec accents",
        values={
            FIELD_PERIODE: "f\u00e9vrier 2024",
            FIELD_MONTANT: "EUR980.45",
            FIELD_ETABLISSEMENT: "Clinique Saint-Roch",
            FIELD_VALIDE: True,
            FIELD_REGLEMENT: "2024-03-05",
        },
    ),
    # -- titles that are WRONG ---------------------------------------------
    Case(
        key="ko-01",
        title="scan_20240301_0001",
        intent="titre incorrect - nom de scanner brut",
        values={
            FIELD_PERIODE: "mars 2024",
            FIELD_MONTANT: "EUR1432.10",
            FIELD_ETABLISSEMENT: "H\u00f4pital Priv\u00e9 de Villeneuve",
            FIELD_VALIDE: False,
            FIELD_REGLEMENT: "2024-04-03",
        },
    ),
    Case(
        key="ko-02",
        title="Document sans nom (2)",
        intent="titre incorrect - libelle par defaut",
        values={
            FIELD_PERIODE: "avril 2024",
            FIELD_MONTANT: "EUR765.00",
            FIELD_ETABLISSEMENT: "CH Valenciennes",
            FIELD_VALIDE: False,
        },
    ),
    Case(
        key="ko-03",
        title="IMG_20240512_113045.pdf",
        intent="titre incorrect - photo de telephone, extension incluse",
        values={
            FIELD_PERIODE: "mai 2024",
            FIELD_MONTANT: "EUR2100.99",
            FIELD_ETABLISSEMENT: "Clinique Saint-Roch",
            FIELD_VALIDE: True,
            FIELD_REGLEMENT: "2024-06-04",
        },
    ),
    # -- titles that are INCONSISTENT --------------------------------------
    Case(
        key="mix-01",
        title="Releve de vacations juin 2024",
        intent="titre incoherent - sans accents, ordre inverse",
        values={
            FIELD_PERIODE: "juin 2024",
            FIELD_MONTANT: "EUR1875.25",
            FIELD_ETABLISSEMENT: "CH Valenciennes",
            FIELD_VALIDE: True,
            FIELD_REGLEMENT: "2024-07-02",
        },
    ),
    Case(
        key="mix-02",
        title="2024-07 / RELEVE VACATIONS",
        intent="titre incoherent - majuscules et separateur different",
        values={
            FIELD_PERIODE: "juillet 2024",
            FIELD_MONTANT: "EUR1543.80",
            FIELD_ETABLISSEMENT: "H\u00f4pital Priv\u00e9 de Villeneuve",
            FIELD_VALIDE: False,
            FIELD_REGLEMENT: "2024-08-06",
        },
    ),
    Case(
        key="mix-03",
        title="ao\u00fbt 2024 \u2014 Relev\u00e9 de vacations (copie)",
        intent="titre incoherent - proche du canonique mais suffixe parasite",
        values={
            FIELD_PERIODE: "ao\u00fbt 2024",
            FIELD_MONTANT: "EUR1120.00",
            FIELD_ETABLISSEMENT: "Clinique Saint-Roch",
            FIELD_VALIDE: True,
        },
    ),
    # -- Periode ABSENT (the template cannot resolve) ----------------------
    Case(
        key="noper-01",
        title="scan_20240915_0042",
        intent="Periode concernee ABSENTE - le template ne peut pas se resoudre",
        values={
            FIELD_MONTANT: "EUR640.00",
            FIELD_ETABLISSEMENT: "CH Valenciennes",
            FIELD_VALIDE: False,
        },
    ),
    Case(
        key="noper-02",
        title="Relev\u00e9 de vacations",
        intent="Periode ABSENTE et titre ambigu - deux docs pourraient collisionner",
        values={
            FIELD_MONTANT: "EUR0.00",
            FIELD_ETABLISSEMENT: "Clinique Saint-Roch",
        },
    ),
    # -- Montant ABSENT ----------------------------------------------------
    Case(
        key="nomont-01",
        title="scan_20241001_0051",
        intent="Montant ABSENT - distinct de zero",
        values={
            FIELD_PERIODE: "octobre 2024",
            FIELD_ETABLISSEMENT: "H\u00f4pital Priv\u00e9 de Villeneuve",
            FIELD_VALIDE: False,
            FIELD_REGLEMENT: "2024-11-05",
        },
    ),
    # -- Montant ZERO ------------------------------------------------------
    Case(
        key="zero-01",
        title="scan_20241102_0060",
        intent="Montant = 0 - piege de faussete, ne PAS confondre avec absent",
        values={
            FIELD_PERIODE: "novembre 2024",
            FIELD_MONTANT: "EUR0.00",
            FIELD_ETABLISSEMENT: "CH Valenciennes",
            FIELD_VALIDE: True,
            FIELD_REGLEMENT: "2024-12-03",
        },
    ),
    Case(
        key="zero-02",
        title=_canonical("d\u00e9cembre 2024"),
        intent="Montant = 0 mais titre deja correct",
        values={
            FIELD_PERIODE: "d\u00e9cembre 2024",
            FIELD_MONTANT: "EUR0.00",
            FIELD_ETABLISSEMENT: "Clinique Saint-Roch",
            FIELD_VALIDE: False,
        },
    ),
    # -- decimals and accents ---------------------------------------------
    Case(
        key="dec-01",
        title="scan_20250115_0071",
        intent="montant decimal non trivial + accents partout",
        values={
            FIELD_PERIODE: "janvier 2025",
            FIELD_MONTANT: "EUR1234.56",
            FIELD_ETABLISSEMENT: "H\u00f4pital Priv\u00e9 de Villeneuve",
            FIELD_VALIDE: True,
            FIELD_REGLEMENT: "2025-02-04",
        },
    ),
    Case(
        key="dec-02",
        title="R\u00c9LEV\u00c9 f\u00e9vrier 2025",
        intent="accents mal places + casse incoherente + centimes",
        values={
            FIELD_PERIODE: "f\u00e9vrier 2025",
            FIELD_MONTANT: "EUR87.05",
            FIELD_ETABLISSEMENT: "Clinique Saint-Roch",
            FIELD_VALIDE: False,
            FIELD_REGLEMENT: "2025-03-04",
        },
    ),
    # -- nearly empty ------------------------------------------------------
    Case(
        key="bare-01",
        title="scan_20250301_0080",
        intent="aucun custom field - le cas le plus pauvre",
        values={},
    ),
    Case(
        key="bare-02",
        # Paperless TRIMS leading/trailing whitespace on write but keeps
        # internal runs (VERIFIED_LIVE 3.1.2). So the stored title is not the
        # one submitted, and a rename engine that compares "what I asked for"
        # against "what is stored" will believe every write failed.
        title="espaces   en   trop",
        intent="espaces internes multiples - Paperless trime les bords, pas l'interieur",
        values={FIELD_PERIODE: "mars 2025", FIELD_MONTANT: "EUR310.00"},
    ),
]


def _document_body(case: Case) -> str:
    lines = [DOCUMENT_TYPE, f"Cas: {case.key}", f"Intention: {case.intent}"]
    for name, value in case.values.items():
        lines.append(f"{name}: {value}")
    return "\n".join(lines) + "\n"


def fetch_all(client: httpx.Client, endpoint: str, **params: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        response = client.get(endpoint, params={"page_size": 100, "page": page, **params})
        response.raise_for_status()
        payload = response.json()
        items.extend(payload.get("results", []))
        if not payload.get("next"):
            return items
        page += 1


def merge_custom_fields(
    existing: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Read-modify-write merge. See ADR-0004 - a partial PATCH deletes fields."""
    merged = {int(item["field"]): dict(item) for item in existing}
    for update in updates:
        merged[int(update["field"])] = dict(update)
    return list(merged.values())


def main() -> int:
    with build_client() as client:
        guard_not_a_real_library(client)

        print("Document type")
        type_id = get_or_create(
            client,
            "/api/document_types/",
            DOCUMENT_TYPE,
            {"name": DOCUMENT_TYPE, "matching_algorithm": 0},
        )

        ids = seed_custom_fields(client)

        # Compare on the STORED form of the title, not the submitted one:
        # Paperless normalises titles on write (it strips surrounding
        # whitespace), so a naive equality test re-uploads those documents on
        # every run and slowly fills the sandbox with duplicates.
        existing_titles = {
            str(document.get("title", "")).strip(): int(document["id"])
            for document in fetch_all(client, "/api/documents/")
        }

        print(f"\nGolden dataset ({len(CASES)} cases)")
        queued = 0
        for case in CASES:
            if case.title in existing_titles:
                print(f"  = {case.key:<10} already present")
                continue
            files = {
                "document": (
                    f"{case.key}.txt",
                    _document_body(case).encode("utf-8"),
                    "text/plain",
                )
            }
            response = client.post(
                "/api/documents/post_document/",
                files=files,
                data={"title": case.title, "document_type": str(type_id)},
            )
            if response.status_code >= 400:
                raise SeedError(f"upload {case.key} failed: {response.status_code} {response.text}")
            queued += 1
            print(f"  + {case.key:<10} {case.intent}")

        print(f"\n{queued} document(s) queued.")
        print(
            "Paperless consumes uploads asynchronously. Once the queue has "
            "drained, re-run this script: it will then apply the custom field "
            "values to the consumed documents."
        )

        # -- second pass: apply values to whatever has been consumed --------
        print("\nApplying custom field values")
        by_title = {
            str(document.get("title", "")): document
            for document in fetch_all(client, "/api/documents/")
        }
        applied = 0
        for case in CASES:
            document = by_title.get(case.title)
            if document is None:
                print(f"  . {case.key:<10} not consumed yet")
                continue

            updates = [
                {"field": ids[name], "value": value}
                for name, value in case.values.items()
                if name in ids
            ]
            updates.append({"field": ids[FIELD_COMMENTAIRE], "value": case.intent})
            updates.append({"field": ids[FIELD_REFERENCE], "value": case.key})

            current = client.get(f"/api/documents/{document['id']}/")
            current.raise_for_status()
            payload = merge_custom_fields(current.json().get("custom_fields") or [], updates)

            response = client.patch(
                f"/api/documents/{document['id']}/", json={"custom_fields": payload}
            )
            if response.status_code >= 400:
                raise SeedError(
                    f"PATCH {case.key} failed: {response.status_code} {response.text}"
                )
            applied += 1
            print(f"  + {case.key:<10} {len(payload)} field(s)")

        print(f"\n{applied} document(s) updated.")
        _summarise(ids)
    return 0


def _summarise(ids: dict[str, int]) -> None:
    def count(predicate: Any) -> int:
        return sum(1 for case in CASES if predicate(case))

    print("\nCoverage of the deliberately imperfect cases:")
    print(f"  titre deja correct        : {count(lambda c: c.key.startswith('ok'))}")
    print(f"  titre incorrect           : {count(lambda c: c.key.startswith('ko'))}")
    print(f"  titre incoherent          : {count(lambda c: c.key.startswith('mix'))}")
    print(f"  Periode presente          : {count(lambda c: FIELD_PERIODE in c.values)}")
    print(f"  Periode ABSENTE           : {count(lambda c: FIELD_PERIODE not in c.values)}")
    print(f"  Montant present           : {count(lambda c: FIELD_MONTANT in c.values)}")
    print(f"  Montant ABSENT            : {count(lambda c: FIELD_MONTANT not in c.values)}")
    print(
        "  Montant = 0               : "
        f"{count(lambda c: c.values.get(FIELD_MONTANT) == 'EUR0.00')}"
    )
    print(f"  total                     : {len(CASES)}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
