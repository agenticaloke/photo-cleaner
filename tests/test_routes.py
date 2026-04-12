import pytest


class TestIndexPage:
    def test_index_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Duplicate Photo Cleaner" in resp.data

    def test_shows_connect_buttons_when_not_connected(self, client):
        resp = client.get("/")
        assert b"Connect Google Drive" in resp.data
        assert b"Connect OneDrive" in resp.data

    def test_shows_scan_button_when_connected(self, client):
        with client.session_transaction() as sess:
            sess["google_connected"] = True
            sess["google_credentials"] = {"token": "fake"}
        resp = client.get("/")
        assert b"Basic Scan" in resp.data


class TestScanPage:
    def test_scan_redirects_if_not_connected(self, client):
        resp = client.get("/scan")
        assert resp.status_code == 302

    def test_scan_page_loads_when_connected(self, client):
        with client.session_transaction() as sess:
            sess["google_connected"] = True
            sess["google_credentials"] = {"token": "fake"}
        resp = client.get("/scan")
        assert resp.status_code == 200
        assert b"Scanning" in resp.data


class TestResultsPage:
    def test_results_redirects_without_scan(self, client):
        resp = client.get("/results")
        assert resp.status_code == 302


class TestDeleteEndpoint:
    def test_delete_get_redirects_home(self, client):
        resp = client.get("/delete")
        assert resp.status_code == 302

    def test_delete_redirects_without_data(self, client):
        resp = client.post("/delete")
        assert resp.status_code == 302


class TestThumbnailProxy:
    def test_returns_404_for_unknown_provider(self, client):
        resp = client.get("/thumbnail/unknown/fakeid")
        assert resp.status_code == 404


class TestAuthRoutes:
    def test_google_login_redirects(self, client, app):
        # Only works if credentials are configured
        app.config["GOOGLE_CLIENT_ID"] = "test-client-id"
        app.config["GOOGLE_CLIENT_SECRET"] = "test-secret"
        resp = client.get("/auth/google/login")
        assert resp.status_code == 302
        assert "accounts.google.com" in resp.headers["Location"]

    def test_google_logout_clears_session(self, client):
        with client.session_transaction() as sess:
            sess["google_connected"] = True
            sess["google_credentials"] = {"token": "fake"}
        resp = client.get("/auth/google/logout")
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "google_connected" not in sess

    def test_microsoft_logout_clears_session(self, client):
        with client.session_transaction() as sess:
            sess["ms_connected"] = True
            sess["ms_token"] = "fake"
        resp = client.get("/auth/microsoft/logout")
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert "ms_connected" not in sess
