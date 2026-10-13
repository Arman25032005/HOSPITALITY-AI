def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_checks_database(client):
    assert client.get("/ready").json() == {"status": "ready"}
