"""确认锁定与一致性：重跑不覆盖已确认组，页面/差异/导出引用同一套结果。"""


def test_confirm_persists_across_match_rerun(client, demo):
    groups = client.get("/api/matches").json()["groups"]
    target = groups[0]["match_id"]
    resp = client.post(f"/api/matches/{target}/confirm")
    assert resp.status_code == 200
    assert resp.json()["confirmed_count"] == 1

    again = client.post("/api/matches").json()
    assert again["confirmed_count"] == 1
    assert [g["match_id"] for g in again["groups"] if g["confirmed"]] == [target]
    assert {g["match_id"] for g in again["groups"]} == {g["match_id"] for g in groups}


def test_confirm_persists_across_demo_rerun(client, demo):
    target = client.get("/api/matches").json()["groups"][0]["match_id"]
    client.post(f"/api/matches/{target}/confirm")
    rerun = client.post("/api/demo/run").json()
    assert rerun["run_id"] == demo["run_id"]
    data = client.get("/api/matches").json()
    assert data["confirmed_count"] == 1
    assert [g["match_id"] for g in data["groups"] if g["confirmed"]] == [target]


def test_unconfirm_releases_group(client, demo):
    target = client.get("/api/matches").json()["groups"][0]["match_id"]
    client.post(f"/api/matches/{target}/confirm")
    resp = client.post(f"/api/matches/{target}/unconfirm")
    assert resp.status_code == 200
    assert resp.json()["confirmed_count"] == 0
    assert all(not g["confirmed"] for g in resp.json()["groups"])


def test_confirm_unknown_match_returns_404(client, demo):
    resp = client.post("/api/matches/m-nope/confirm")
    assert resp.status_code == 404
    assert resp.json()["error"] == "E_NO_MATCH"


def test_decisions_cover_every_candidate(client, demo):
    data = client.get("/api/matches").json()
    candidates = client.get("/api/candidates").json()["candidates"]
    decisions = data["decisions"]
    assert len(decisions) == len(candidates)
    chosen = [item for item in decisions if item["decision"] in ("chosen", "confirmed")]
    assert len(chosen) == data["count"]
    for item in decisions:
        if item["decision"] == "dropped":
            assert item["blocked_by"], item


def test_export_and_pages_share_same_result(client, demo):
    data = client.get("/api/matches").json()
    report = client.get("/api/export/report.csv").text
    for group in data["groups"]:
        assert group["match_id"] in report
    summary = client.get("/api/export/summary").json()
    diff = client.get("/api/differences").json()
    assert summary["counts"]["matched_groups"] == diff["counts"]["matched_groups"] == data["count"]
    assert summary["counts"]["confirmed_groups"] == diff["counts"]["confirmed_groups"]
    assert summary["counts"]["dropped_candidates"] == diff["counts"]["dropped_candidates"]
    manifest = client.get("/api/export/manifest.json").json()
    assert manifest["counts"]["matched_groups"] == data["count"]


def test_matches_page_shows_review_sections(client, demo):
    html = client.get("/matches").text
    assert "最终组合" in html
    assert "被放弃的候选" in html
    assert "预计差额" in html
    assert "确认" in html
