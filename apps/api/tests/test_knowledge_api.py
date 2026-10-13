from tests.conftest import DEMO_PASSWORD, HOTEL_ID

BASE = f"/api/v1/hotels/{HOTEL_ID}/knowledge"
POLICY = "# Pool Rules\n\n## Diving\n\nDiving is prohibited in the rooftop pool at all times.\n"


def upload(client, headers, content=POLICY, name="pool.md", title="Pool Rules", **fields):
    data = {"title": title, "doc_type": "policy", **fields}
    return client.post(f"{BASE}/documents", data=data, files={"file": (name, content.encode()
                       if isinstance(content, str) else content)}, headers=headers)


def ask(client, headers, q, audience="guest"):
    return client.post(f"{BASE}/ask", json={"question": q, "audience": audience},
                       headers=headers).json()


def test_upload_index_and_ask_roundtrip(client, auth):
    mgr = auth("manager@demo.hotel")
    r = upload(client, mgr)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "indexed" and doc["is_active"] and doc["version"] == 1
    detail = client.get(f"{BASE}/documents/{doc['id']}", headers=mgr).json()
    assert detail["chunk_count"] >= 1 and detail["jobs"][0]["status"] == "succeeded"
    ans = ask(client, mgr, "Is diving allowed in the pool?")
    assert not ans["abstained"] and "prohibited" in ans["answer"]
    assert ans["citations"][0]["title"] == "Pool Rules"


def test_roles_enforced(client, auth):
    assert upload(client, auth("reception@demo.hotel")).status_code == 403
    assert upload(client, auth("housekeeping@demo.hotel")).status_code == 403
    assert client.get(f"{BASE}/documents", headers=auth("housekeeping@demo.hotel")).status_code \
        == 403
    assert client.get(f"{BASE}/documents", headers=auth("reception@demo.hotel")).status_code == 200
    assert client.get(f"{BASE}/documents").status_code == 401


def test_invalid_uploads_rejected_without_creating_documents(client, auth):
    mgr = auth("manager@demo.hotel")
    before = len(client.get(f"{BASE}/documents", headers=mgr).json())
    assert upload(client, mgr, name="x.exe", content=b"MZ").status_code == 422
    assert upload(client, mgr, content=b"").status_code == 422
    assert upload(client, mgr, content=b"x" * (5 * 1024 * 1024 + 1)).status_code == 422
    assert upload(client, mgr, title="  ").status_code == 422
    bad = client.post(f"{BASE}/documents", data={"title": "T", "doc_type": "nonsense"},
                      files={"file": ("a.md", b"hello")}, headers=mgr)
    assert bad.status_code == 422
    assert len(client.get(f"{BASE}/documents", headers=mgr).json()) == before


def test_unparseable_upload_is_422_and_recorded_as_failed(client, auth):
    mgr = auth("manager@demo.hotel")
    r = upload(client, mgr, content=b"%PDF-1.4 garbage", name="b.pdf", title="Broken PDF")
    assert r.status_code == 422 and "internal" not in r.text.lower()
    failed = [d for d in client.get(f"{BASE}/documents", headers=mgr).json()
              if d["title"] == "Broken PDF"]
    assert failed and failed[0]["status"] == "failed" and not failed[0]["is_active"]
    detail = client.get(f"{BASE}/documents/{failed[0]['id']}", headers=mgr).json()
    assert detail["jobs"][0]["status"] == "failed" and detail["jobs"][0]["error"]


def test_replace_deactivate_reindex_delete_lifecycle(client, auth):
    mgr = auth("manager@demo.hotel")
    v1 = upload(client, mgr, doc_key="pool-rules").json()
    v2 = upload(client, mgr, content=POLICY.replace("prohibited", "allowed only in the deep end"),
                doc_key="pool-rules").json()
    assert v2["version"] == 2
    docs = {d["version"]: d for d in client.get(f"{BASE}/documents", headers=mgr).json()
            if d["doc_key"] == "pool-rules"}
    assert docs[1]["is_active"] is False and docs[2]["is_active"] is True
    assert "deep end" in ask(client, mgr, "Is diving allowed in the pool?")["answer"]

    assert client.post(f"{BASE}/documents/{v2['id']}/reindex", headers=mgr).status_code == 200
    assert "deep end" in ask(client, mgr, "Is diving allowed in the pool?")["answer"]

    off = client.post(f"{BASE}/documents/{v2['id']}/deactivate", headers=mgr).json()
    assert off["is_active"] is False
    assert ask(client, mgr, "Is diving allowed in the pool?")["abstained"]

    assert client.delete(f"{BASE}/documents/{v2['id']}", headers=mgr).status_code == 204
    assert client.get(f"{BASE}/documents/{v2['id']}", headers=mgr).status_code == 404
    assert client.get(f"{BASE}/documents/{v1['id']}", headers=mgr).status_code == 200


def test_confidential_upload_hidden_from_guest_audience(client, auth):
    mgr = auth("manager@demo.hotel")
    upload(client, mgr, content="# Safe Code\n\nThe vault code rotation schedule is weekly.",
           title="Vault Procedure", is_confidential="true")
    assert ask(client, mgr, "vault code rotation schedule", "guest")["abstained"]
    assert not ask(client, mgr, "vault code rotation schedule", "staff")["abstained"]


def test_search_endpoint_returns_scored_passages(client, auth):
    r = client.post(f"{BASE}/search", json={"query": "parking cost", "top_k": 3},
                    headers=auth("reception@demo.hotel"))
    assert r.status_code == 200 and 1 <= len(r.json()) <= 3
    assert r.json()[0]["title"] == "Hotel Amenities"


def test_other_tenant_cannot_touch_knowledge(client, auth, other_org):
    tok = client.post("/api/v1/auth/login", json={"email": other_org["email"],
                                                  "password": DEMO_PASSWORD}).json()
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    mgr = auth("manager@demo.hotel")
    doc_id = client.get(f"{BASE}/documents", headers=mgr).json()[0]["id"]
    assert client.get(f"{BASE}/documents", headers=h).status_code == 404
    assert client.get(f"{BASE}/documents/{doc_id}", headers=h).status_code == 404
    assert upload(client, h).status_code == 404
    assert client.post(f"{BASE}/ask", json={"question": "check-out"}, headers=h).status_code == 404
    assert client.delete(f"{BASE}/documents/{doc_id}", headers=h).status_code == 404
    # and the other tenant's own hotel has no knowledge base yet
    own = client.post(f"/api/v1/hotels/{other_org['hotel_id']}/knowledge/ask",
                      json={"question": "What time is check-out?"}, headers=h).json()
    assert own["abstained"] is True
