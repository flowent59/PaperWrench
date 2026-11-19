"""Private CRUD for reusable criteria."""

from fastapi.testclient import TestClient

from paperwrench.auth import AuthSession


def test_criteria_round_trip_and_owner_isolation(
    client: TestClient, auth_record: AuthSession,
) -> None:
    prefix = "/api/v1/filter-library"
    definition = {
        "version": 1,
        "filters": {"root": {"kind": "group", "operator": "and", "children": [{
            "kind": "condition", "field": {"source": "core", "name": "correspondent"},
            "operator": "is_missing", "value": None,
        }]}},
    }
    body = {"name": "Missing sender", "description": "Invoice review", "definition": definition}
    created = client.post(prefix, json=body)
    assert created.status_code == 201, created.text
    copy = created.json()["definition"]
    path = f"{prefix}/{created.json()['id']}"
    assert copy == definition
    assert client.post(prefix, json=body).status_code == 409
    invalid = {**body, "definition": {**definition, "version": 2}}
    assert client.put(path, json=invalid).status_code == 422
    assert client.put(path, json={**body, "definition": {
        "version": 1, "filters": {"root": {"children": []}},
    }}).status_code == 200
    assert copy == definition

    previous = auth_record.paperless_user_id
    auth_record.paperless_user_id = previous + 1
    assert client.get(prefix).json() == []
    assert client.put(path, json=body).status_code == 404
    assert client.delete(path).status_code == 404
    auth_record.paperless_user_id = previous
    assert client.delete(path).status_code == 204
    assert client.get(prefix).json() == []
