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
        assert "Add an event" in home or "Document plan" in home
        assert "Private renewal agent" in home
    assert True


def test_event_form_lists_event_types() -> None:
    with TestClient(app) as client:
        response = client.get("/events/new")
    assert response.status_code == 200
    assert 'name="event_type"' in response.text
    assert "Domestic travel" in response.text
    assert "Job start" in response.text
    assert 'name="destination_country"' in response.text
    assert 'name="home_location"' in response.text


def test_dashboard_leads_with_the_expiry_watch() -> None:
    with TestClient(app) as client:
        home = client.get("/").text
    assert "Document watch" in home
    assert "Expiry watch" in home
    assert home.index("Expiry watch") < home.index("Event plan")
