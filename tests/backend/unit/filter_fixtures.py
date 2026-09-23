"""A field catalogue modelled on the Golden Dataset, for the pure filter tests.

The custom fields here mirror ``scripts/seed_dev_golden_dataset.py`` - same
names, same data types, same accents - so a compiler unit test and a live
test are talking about the same shapes. The two select/url/int/float/document
-link fields are additions: the Golden Dataset does not exercise those types,
and the compiler must still be exhaustively covered for them.
"""

from __future__ import annotations

from paperwrench.filters import CoreField
from paperwrench.filters import CoreFieldRef
from paperwrench.filters import CustomFieldRef
from paperwrench.filters import FieldCatalog
from paperwrench.filters import FilterCondition
from paperwrench.filters import FilterGroup
from paperwrench.filters import FilterOperator
from paperwrench.filters import FilterSet
from paperwrench.filters import GroupOperator
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldDataType

#: Ids are arbitrary but stable, and deliberately not sequential-by-type:
#: nothing in the engine may infer a type from an id.
PERIODE = 1  # string   - "Période concernée"
MONTANT = 2  # monetary - "Montant"
VALIDE = 3  # boolean  - "Validé"
REGLEMENT = 4  # date     - "Date de règlement"
CATEGORIE = 7  # select
COMMENTAIRE = 8  # longtext
LIEN = 11  # url
NB_VACATIONS = 12  # integer
TAUX = 13  # float
PIECE_JOINTE = 14  # documentlink

OPTION_URGENT = "gsbRSetmXYcC2nKx"
OPTION_NORMAL = "kQ4tXbYcZ2mNp7Rd"


def golden_custom_fields() -> list[CustomField]:
    return [
        CustomField(
            id=PERIODE, name="Période concernée", data_type=CustomFieldDataType.STRING
        ),
        CustomField(
            id=MONTANT,
            name="Montant",
            data_type=CustomFieldDataType.MONETARY,
            extra_data={"default_currency": "EUR"},
        ),
        CustomField(id=VALIDE, name="Validé", data_type=CustomFieldDataType.BOOLEAN),
        CustomField(
            id=REGLEMENT, name="Date de règlement", data_type=CustomFieldDataType.DATE
        ),
        CustomField(
            id=CATEGORIE,
            name="Catégorie",
            data_type=CustomFieldDataType.SELECT,
            extra_data={
                "select_options": [
                    {"id": OPTION_URGENT, "label": "Urgent"},
                    {"id": OPTION_NORMAL, "label": "Normal"},
                ]
            },
        ),
        CustomField(
            id=COMMENTAIRE, name="Commentaire", data_type=CustomFieldDataType.LONG_TEXT
        ),
        CustomField(id=LIEN, name="Lien", data_type=CustomFieldDataType.URL),
        CustomField(
            id=NB_VACATIONS, name="Nombre de vacations", data_type=CustomFieldDataType.INTEGER
        ),
        CustomField(id=TAUX, name="Taux", data_type=CustomFieldDataType.FLOAT),
        CustomField(
            id=PIECE_JOINTE,
            name="Pièce jointe",
            data_type=CustomFieldDataType.DOCUMENT_LINK,
        ),
    ]


def catalog() -> FieldCatalog:
    return FieldCatalog(golden_custom_fields())


def custom(field_id: int, operator: FilterOperator, value: object = None) -> FilterCondition:
    return FilterCondition(
        field=CustomFieldRef(field_id=field_id), operator=operator, value=value
    )


def core(name: CoreField, operator: FilterOperator, value: object = None) -> FilterCondition:
    return FilterCondition(field=CoreFieldRef(name=name), operator=operator, value=value)


def and_group(*children: object) -> FilterGroup:
    return FilterGroup(operator=GroupOperator.AND, children=list(children))


def or_group(*children: object) -> FilterGroup:
    return FilterGroup(operator=GroupOperator.OR, children=list(children))


def filterset(*children: object) -> FilterSet:
    return FilterSet(root=and_group(*children))
