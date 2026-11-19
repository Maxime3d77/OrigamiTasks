"""UI version is rendered from the running backend, never hardcoded.

Regression for the screenshot/footer displaying an obsolete release after an
otherwise successful Docker deployment.
"""
from fastapi.testclient import TestClient
from app import config
from app.main import app


def test_ui_reports_effective_backend_version_and_cache_keys():
    with TestClient(app) as client:
        page = client.get("/")
        health = client.get("/api/health")
        assert page.status_code == 200
        assert health.status_code == 200
        expected = health.json()["version"]
        assert expected == config.APP_VERSION
        assert "v" + expected in page.text
        assert "styles.css?v=" + expected in page.text
        assert "login.js?v=" + expected in page.text
        assert "app.js?v=" + expected in page.text
        assert "__APP_VERSION__" not in page.text
