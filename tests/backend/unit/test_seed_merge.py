"""The seed script's merge is the reference read-modify-write implementation.

If this merge can drop a field, so can PaperWrench's writer. Testing it here
keeps the dangerous pattern honest even though the script itself only ever
runs against the disposable dev instance.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

seed_values = pytest.importorskip(
    "seed_dev_custom_field_values",
    reason="seed scripts require httpx, which is a runtime dependency",
)
seed = pytest.importorskip("seed_dev_paperless")

merge_custom_fields = seed_values.merge_custom_fields


def test_merge_preserves_fields_that_are_not_being_updated() -> None:
    """The whole NestedUpdateMixin hazard in one assertion."""
    existing: list[dict[str, Any]] = [
        {"field": 1, "value": "janvier 2024"},
        {"field": 2, "value": "EUR120.50"},
        {"field": 3, "value": "REF-2024-0000"},
        {"field": 4, "value": "CH Valenciennes"},
    ]

    merged = merge_custom_fields(existing, [{"field": 2, "value": "EUR999.99"}])

    by_id = {item["field"]: item["value"] for item in merged}
    assert by_id == {
        1: "janvier 2024",
        2: "EUR999.99",
        3: "REF-2024-0000",
        4: "CH Valenciennes",
    }


def test_merge_adds_fields_that_did_not_exist() -> None:
    merged = merge_custom_fields(
        [{"field": 1, "value": "a"}], [{"field": 7, "value": "new"}]
    )

    assert {item["field"] for item in merged} == {1, 7}


def test_merge_tolerates_string_field_ids_from_the_api() -> None:
    """Paperless has returned ids as both int and str across versions."""
    merged = merge_custom_fields(
        [{"field": "2", "value": "old"}], [{"field": 2, "value": "new"}]
    )

    assert len(merged) == 1
    assert merged[0]["value"] == "new"


def test_merge_does_not_mutate_its_inputs() -> None:
    existing = [{"field": 1, "value": "original"}]
    updates = [{"field": 1, "value": "changed"}]

    merge_custom_fields(existing, updates)

    assert existing == [{"field": 1, "value": "original"}]
    assert updates == [{"field": 1, "value": "changed"}]


def test_merge_of_an_empty_update_is_a_no_op() -> None:
    existing = [{"field": 1, "value": "a"}, {"field": 2, "value": "b"}]

    assert merge_custom_fields(existing, []) == existing


@pytest.mark.parametrize(
    "url",
    [
        # Substring matching would wrongly accept all of these.
        "https://paperless.my-real-domain.example",
        "https://docs.example.com/paperless",
        "https://localhost.attacker.example",
        "https://192.168.1.50:8000",
        "https://127.0.0.1.attacker.example",
    ],
)
def test_seed_refuses_any_host_that_is_not_the_dev_sandbox(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("PAPERLESS_URL", url)

    with pytest.raises(seed.SeedError, match="Refusing to seed"):
        seed.build_client()


def test_seed_accepts_the_dev_sandbox_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless:8000")

    with seed.build_client() as client:
        assert client.headers["Accept"].endswith("version=10")


def test_seeded_fields_include_several_collateral_fields() -> None:
    """Fewer than three extra fields would make the hazard easy to miss."""
    assert len(seed.COLLATERAL_FIELDS) >= 3


def test_seeded_names_use_real_french_accents() -> None:
    names = [seed.FIELD_PERIODE, seed.DOCUMENT_TYPE, *(n for n, _ in seed.COLLATERAL_FIELDS)]
    assert any(any(ord(c) > 127 for c in name) for name in names)
    # A field name containing a space is the case that breaks naive query building.
    assert " " in seed.FIELD_PERIODE
