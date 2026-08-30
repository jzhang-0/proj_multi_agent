"""WEB-003:本机认证会话、Host 校验、无 CORS、静态健康页。"""

from fastapi.testclient import TestClient

from web.app import allowed_hosts, create_app
from web.auth import COOKIE_NAME, WebSession

PORT = 8787


def make_client() -> tuple[TestClient, WebSession]:
    session = WebSession.generate()
    app = create_app(session=session, port=PORT)
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    return client, session


def test_root_without_session_is_unauthorized():
    client, _session = make_client()

    response = client.get("/")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_valid_token_sets_cookie_and_redirects_without_token():
    client, session = make_client()

    response = client.get(f"/?token={session.token}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    set_cookie = response.headers["set-cookie"]
    assert f"{COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


def test_invalid_token_is_rejected():
    client, _session = make_client()

    response = client.get("/?token=not-the-real-token")

    assert response.status_code == 401


def test_cookie_from_valid_token_grants_access_to_health_page():
    client, session = make_client()
    client.get(f"/?token={session.token}")  # 换取 cookie

    response = client.get("/")

    assert response.status_code == 200
    assert "amux web" in response.text


def test_spa_routes_keep_session_auth_and_support_direct_navigation():
    client, session = make_client()

    assert client.get("/task/T-014").status_code == 401
    client.get(f"/?token={session.token}")

    for path in ("/task/T-014", "/timeline", "/workspace", "/help"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


def test_forged_cookie_is_rejected():
    client, _session = make_client()
    client.cookies.set(COOKIE_NAME, "guessed-session-id")

    response = client.get("/")

    assert response.status_code == 401


def test_wrong_host_header_is_rejected_even_with_valid_cookie():
    client, session = make_client()
    client.get(f"/?token={session.token}")

    response = client.get("/", headers={"host": "evil.example:8787"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_localhost_host_header_is_also_accepted():
    client, session = make_client()
    client.get(f"/?token={session.token}")

    response = client.get("/", headers={"host": f"localhost:{PORT}"})

    assert response.status_code == 200


def test_no_cors_headers_are_ever_set():
    client, session = make_client()
    client.get(f"/?token={session.token}")

    response = client.get("/", headers={"origin": "http://evil.example"})

    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_docs_endpoints_are_disabled():
    client, session = make_client()
    client.get(f"/?token={session.token}")

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_allowed_hosts_only_covers_this_port():
    hosts = allowed_hosts(PORT)

    assert hosts == {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
    assert f"127.0.0.1:{PORT + 1}" not in hosts
