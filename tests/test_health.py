def test_health_reports_ok(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_path_uses_the_error_envelope(client):
    response = client.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
