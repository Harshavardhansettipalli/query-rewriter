from tests.conftest import INSUFFICIENT, SUFFICIENT, FakeLLM, create_user


def rewrite(client, user_id, query):
    return client.post("/api/rewrite", json={"user_id": user_id, "query": query})


# ------------------------------------------------------------------ system
def test_health_reports_ai_state(make_client):
    assert make_client().get("/api/health").json() == {
        "status": "ok", "ai_enabled": True, "model": "gpt-4o-mini", "database": "ok"}
    assert make_client(use_llm=False).get("/api/health").json()["ai_enabled"] is False


def test_frontend_is_served(make_client):
    res = make_client().get("/")
    assert res.status_code == 200 and "Query Rewriter" in res.text
    assert res.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------- profiles
def test_profile_crud_and_validation(make_client):
    client = make_client()
    user = create_user(client, interests=["SQL", "sql", "  Power   BI "])
    assert user["interests"] == ["SQL", "Power BI"]  # deduped + cleaned

    assert client.post("/api/users", json={"name": "X", "email": "bad"}).json()["detail"] == "Email address is not valid."
    assert client.post("/api/users", json={"name": "Y", "email": "PRIYA@example.com"}).status_code == 409

    res = client.put(f"/api/users/{user['id']}", json={**user, "role": "Data scientist"})
    assert res.status_code == 200 and res.json()["role"] == "Data scientist"

    assert client.get("/api/users/999").status_code == 404
    assert client.delete(f"/api/users/{user['id']}").status_code == 204
    assert client.get("/api/users").json() == []


def test_activity_add_list_delete(make_client):
    client = make_client()
    uid = create_user(client)["id"]
    res = client.post(f"/api/users/{uid}/activities", json={"event_type": "view", "content": "SQL joins course"})
    assert res.status_code == 201
    assert client.post(f"/api/users/{uid}/activities", json={"event_type": "hack", "content": "x"}).status_code == 422
    items = client.get(f"/api/users/{uid}/activities").json()
    assert [a["content"] for a in items] == ["SQL joins course"]
    assert items[0]["created_at"].endswith("Z")  # browsers must read this as UTC
    assert client.delete(f"/api/users/{uid}/activities/{items[0]['id']}").status_code == 204


# ----------------------------------------------------------------- pipeline
def test_specific_query_uses_one_ai_call_and_no_profile(make_client):
    llm = FakeLLM(understanding=SUFFICIENT)
    client = make_client(llm)
    uid = create_user(client)["id"]
    body = rewrite(client, uid, "python pandas tutorial").json()

    assert llm.stages() == ["understand"]
    assert body["changed"] is False
    assert body["suggested_query"] == "python pandas tutorial"
    statuses = {s["key"]: s["status"] for s in body["steps"]}
    assert statuses == {"preprocess": "done", "understand": "done", "context": "skipped",
                        "rewrite": "skipped", "approval": "pending"}


def test_vague_query_adds_context_without_leaking_identity(make_client):
    llm = FakeLLM(understanding=INSUFFICIENT, rewrite={
        "rewritten_query": "intermediate SQL courses for data analysts",
        "context_added": ["role: data analyst", "level: intermediate"],
        "explanation": "Added your role and level."})
    client = make_client(llm)
    uid = create_user(client)["id"]
    client.post(f"/api/users/{uid}/activities", json={"event_type": "view", "content": "SQL window functions"})
    body = rewrite(client, uid, "courses").json()

    assert llm.stages() == ["understand", "rewrite"]
    assert body["changed"] is True
    assert body["suggested_query"] == "intermediate SQL courses for data analysts"
    assert body["context_used"] == ["role: data analyst", "level: intermediate"]
    rewrite_prompt = llm.calls[1][1]
    assert "SQL window functions" in rewrite_prompt  # activity is used
    assert "priya@example.com" not in rewrite_prompt and "Priya" not in rewrite_prompt  # identity is not


def test_ai_spelling_fixes_are_applied_by_python(make_client):
    understanding = {**SUFFICIENT, "spelling_corrections": [
        {"original": "pyhton", "corrected": "python"},
        {"original": "notinquery", "corrected": "injected"}]}  # must be ignored
    client = make_client(FakeLLM(understanding=understanding))
    uid = create_user(client)["id"]
    body = rewrite(client, uid, "Pyhton tutorial").json()
    assert body["suggested_query"] == "Python tutorial"
    assert body["spelling_fixes"] == [{"original": "pyhton", "corrected": "python"}]
    assert body["explanation"] == "Fixed spelling: pyhton → python."


def test_unsafe_rewrite_is_rejected_by_guardrail(make_client):
    llm = FakeLLM(understanding=INSUFFICIENT, rewrite={
        "rewritten_query": "buy cheap crypto now", "context_added": [], "explanation": "x"})
    client = make_client(llm)
    uid = create_user(client)["id"]
    body = rewrite(client, uid, "machine learning courses").json()
    assert body["suggested_query"] == "machine learning courses"
    rewrite_step = next(s for s in body["steps"] if s["key"] == "rewrite")
    assert rewrite_step["status"] == "fallback" and "safety check" in rewrite_step["summary"]


def test_ai_outage_falls_back_to_dictionary_spelling(make_client):
    client = make_client(FakeLLM(fail_on="understand"))
    uid = create_user(client)["id"]
    res = rewrite(client, uid, "pyhton tutorial")
    assert res.status_code == 200
    body = res.json()
    assert body["degraded"] is True and "Simulated OpenAI outage" in body["notice"]
    assert body["suggested_query"] == "python tutorial"


def test_rewrite_outage_keeps_corrected_query(make_client):
    client = make_client(FakeLLM(understanding=INSUFFICIENT, fail_on="rewrite"))
    uid = create_user(client)["id"]
    body = rewrite(client, uid, "courses").json()
    assert body["degraded"] is True and body["suggested_query"] == "courses"


def test_works_without_api_key(make_client):
    client = make_client(use_llm=False)
    uid = create_user(client)["id"]
    body = rewrite(client, uid, "developr jobs").json()
    assert body["llm_used"] is False and body["suggested_query"] == "developer jobs"
    assert "OPENAI_API_KEY" in body["notice"]


def test_duplicate_query_reuses_result_without_ai(make_client):
    llm = FakeLLM(understanding=SUFFICIENT)
    client = make_client(llm)
    uid = create_user(client)["id"]
    first = rewrite(client, uid, "Python pandas tutorial").json()
    second = rewrite(client, uid, "python  pandas tutorial!").json()
    assert llm.stages() == ["understand"]  # second request made no AI call
    assert second["reused_from"] == first["query_id"]
    assert second["query_id"] != first["query_id"]


def test_profile_change_invalidates_duplicate_cache(make_client):
    llm = FakeLLM(understanding=SUFFICIENT)
    client = make_client(llm)
    user = create_user(client)
    rewrite(client, user["id"], "python pandas tutorial")
    client.put(f"/api/users/{user['id']}", json={**user, "role": "Engineer"})
    rewrite(client, user["id"], "python pandas tutorial")
    assert llm.stages() == ["understand", "understand"]


def test_invalid_queries_return_readable_errors(make_client):
    client = make_client()
    uid = create_user(client)["id"]
    assert rewrite(client, uid, "   ").json()["detail"] == "Enter a search query."
    assert "up to 300" in rewrite(client, uid, "a" * 400).json()["detail"]
    assert rewrite(client, 999, "python").status_code == 404


def test_rate_limit(make_client):
    client = make_client(rate_limit_per_minute=2)
    uid = create_user(client)["id"]
    assert rewrite(client, uid, "one query").status_code == 200
    assert rewrite(client, uid, "two query").status_code == 200
    res = rewrite(client, uid, "three query")
    assert res.status_code == 429 and "Retry-After" in res.headers


# ---------------------------------------------------------------- decisions
def test_accept_records_history_and_activity(make_client):
    llm = FakeLLM(understanding=INSUFFICIENT, rewrite={
        "rewritten_query": "SQL courses for analysts", "context_added": [], "explanation": ""})
    client = make_client(llm)
    uid = create_user(client)["id"]
    qid = rewrite(client, uid, "courses").json()["query_id"]

    res = client.post(f"/api/queries/{qid}/decision", json={"action": "accept"})
    assert res.json() == {"query_id": qid, "status": "accepted", "final_query": "SQL courses for analysts"}
    assert client.post(f"/api/queries/{qid}/decision", json={"action": "reject"}).status_code == 409

    history = client.get(f"/api/users/{uid}/queries").json()
    assert history[0]["status"] == "accepted" and history[0]["final_query"] == "SQL courses for analysts"
    activity = client.get(f"/api/users/{uid}/activities").json()
    assert activity[0] == {**activity[0], "event_type": "search", "content": "SQL courses for analysts"}


def test_edit_and_reject(make_client):
    client = make_client()
    uid = create_user(client)["id"]
    q1 = rewrite(client, uid, "first query").json()["query_id"]
    assert client.post(f"/api/queries/{q1}/decision", json={"action": "edit"}).status_code == 422
    res = client.post(f"/api/queries/{q1}/decision", json={"action": "edit", "edited_query": "  my   edit "})
    assert res.json()["final_query"] == "my edit" and res.json()["status"] == "edited"

    q2 = rewrite(client, uid, "second query").json()["query_id"]
    res = client.post(f"/api/queries/{q2}/decision", json={"action": "reject"})
    assert res.json()["final_query"] == "second query" and res.json()["status"] == "rejected"


def test_deleting_profile_removes_its_data(make_client):
    client = make_client()
    uid = create_user(client)["id"]
    qid = rewrite(client, uid, "python").json()["query_id"]
    client.delete(f"/api/users/{uid}")
    assert client.post(f"/api/queries/{qid}/decision", json={"action": "accept"}).status_code == 404


def test_fallback_only_applies_single_typo_fixes(make_client):
    client = make_client(use_llm=False)
    uid = create_user(client)["id"]
    body = rewrite(client, uid, "<img> tutorial").json()
    assert body["suggested_query"] == "<img> tutorial"
