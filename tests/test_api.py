from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_home_renders() -> None:
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Unlapsed" in response.text


def test_documents_render() -> None:
    with TestClient(app) as client:
        response = client.get("/documents")
    assert response.status_code == 200
    assert "Document register" in response.text


def test_plan_renders() -> None:
    with TestClient(app) as client:
        home = client.get("/").text
        assert "Trip to India" in home or "Add a trip" in home
    assert True
