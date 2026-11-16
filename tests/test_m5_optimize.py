"""M5 全局择一：一对多让位于完整组合、统一取舍、确认锁定与取舍轨迹。"""
from decimal import Decimal

from app.modules.m4_candidates import candidate
from app.modules.m5_matching import solve


def bank_record(txn, amount, posted="2026-03-10T01:00:00+00:00", direction=-1):
    return {
        "side": "bank", "seq": 0, "row_no": 1, "txn_no": txn,
        "account": "6222-0012", "posted_at": posted, "direction": direction,
        "amount": amount, "fee": "0.00", "memo": "", "counterparty": "",
        "status": "valid", "reason": "", "flags": [], "raw": [],
    }


def ledger_record(txn, amount, posted="2026-03-10T01:00:00+00:00", direction=-1, seq=0):
    return {
        "side": "ledger", "seq": seq, "row_no": 2 + seq, "txn_no": txn,
        "account": "6222-0012", "posted_at": posted, "direction": direction,
        "amount": amount, "fee": "0.00", "memo": "", "counterparty": "",
        "status": "valid", "reason": "", "flags": [], "raw": [],
    }


def split_records():
    """一笔 3000 流水：可一对一（V3），也可一对多（V1+V2 完整拆分）。"""
    return [
        bank_record("B1", "3000.00"),
        ledger_record("V1", "1800.00", seq=0),
        ledger_record("V2", "1200.00", seq=1),
        ledger_record("V3", "3000.00", seq=2),
    ]


def test_one_to_many_beats_competing_one_to_one(settings):
    # 一对一不再提前占用流水：完整拆分的一对多（3 行）优先于一对一（2 行）
    records = split_records()
    candidates = candidate.build(records, settings)
    result = solve.solve(candidates, records, settings)
    groups = result["groups"]
    assert len(groups) == 1
    assert groups[0]["tier"] == 2
    assert groups[0]["bank_ids"] == ["B1"]
    assert groups[0]["ledger_ids"] == ["V1", "V2"]


def test_dropped_decision_names_blocking_group(settings):
    records = split_records()
    candidates = candidate.build(records, settings)
    result = solve.solve(candidates, records, settings)
    dropped = [item for item in result["decisions"] if item["decision"] == "dropped"]
    assert len(dropped) == 1
    assert dropped[0]["bank_ids"] == ["B1"]
    assert dropped[0]["ledger_ids"] == ["V3"]
    assert dropped[0]["blocked_by"] == [result["groups"][0]["match_id"]]
    chosen = [item for item in result["decisions"] if item["decision"] == "chosen"]
    assert len(chosen) == 1 and chosen[0]["match_id"] == result["groups"][0]["match_id"]


def test_total_diff_minimized_across_pairs(settings):
    # 交叉候选下总差额最小：B1↔V1、B2↔V2 各差 0，交叉则差 0.02
    records = [
        bank_record("B1", "100.00"),
        bank_record("B2", "100.02"),
        ledger_record("V1", "100.00", seq=0),
        ledger_record("V2", "100.02", seq=1),
    ]
    candidates = candidate.build(records, settings)
    assert len(candidates) == 4
    result = solve.solve(candidates, records, settings)
    pairs = {(group["bank_ids"][0], group["ledger_ids"][0]) for group in result["groups"]}
    assert pairs == {("B1", "V1"), ("B2", "V2")}
    assert sum((group["diff"] for group in result["groups"]), Decimal("0.00")) == Decimal("0.00")


def test_confirmed_group_survives_resolve(settings):
    # 已确认组（哪怕全局更劣）在重跑时保留，竞争候选被它挡住
    records = split_records()
    candidates = candidate.build(records, settings)
    confirmed = [{
        "match_id": "m-kept", "tier": 1, "bank_ids": ["B1"], "ledger_ids": ["V3"],
        "bank_sum": "3000.00", "ledger_sum": "3000.00", "diff": "0.00", "day_gap": 0,
        "bank_signed": "-3000.00", "ledger_signed": "-3000.00", "fee_actual": "0.00",
    }]
    result = solve.solve(candidates, records, settings, confirmed=confirmed)
    assert [group["match_id"] for group in result["groups"]] == ["m-kept"]
    decisions = {item["cand_id"]: item for item in result["decisions"]}
    roles = {item["decision"] for item in decisions.values()}
    assert roles == {"confirmed", "dropped"}
    dropped = [item for item in decisions.values() if item["decision"] == "dropped"]
    assert len(dropped) == 1
    assert dropped[0]["ledger_ids"] == ["V1", "V2"]
    assert dropped[0]["blocked_by"] == ["m-kept"]


def test_confirmed_rows_leave_pool_so_other_rows_still_solve(settings):
    # 确认组只占用自己的行，其余行照常参与全局择一
    records = split_records() + [
        bank_record("B9", "700.00"),
        ledger_record("V9", "700.00", seq=3),
    ]
    candidates = candidate.build(records, settings)
    confirmed = [{
        "match_id": "m-kept", "tier": 1, "bank_ids": ["B1"], "ledger_ids": ["V3"],
        "bank_sum": "3000.00", "ledger_sum": "3000.00", "diff": "0.00", "day_gap": 0,
        "bank_signed": "-3000.00", "ledger_signed": "-3000.00", "fee_actual": "0.00",
    }]
    result = solve.solve(candidates, records, settings, confirmed=confirmed)
    ids = {group["match_id"] for group in result["groups"]}
    assert len(ids) == 2 and "m-kept" in ids
    other = [group for group in result["groups"] if group["match_id"] != "m-kept"][0]
    assert other["bank_ids"] == ["B9"] and other["ledger_ids"] == ["V9"]


def test_same_amount_same_day_picks_smaller_ids(settings):
    # 同额同日两条候选：统一取业务号小者，与候选传入顺序无关
    records = [
        bank_record("B1", "500.00"),
        ledger_record("V900", "500.00", seq=0),
        ledger_record("V800", "500.00", seq=1),
    ]
    candidates = candidate.build(records, settings)
    forward = solve.solve(candidates, records, settings)["groups"]
    backward = solve.solve(list(reversed(candidates)), records, settings)["groups"]
    assert len(forward) == 1
    assert forward[0]["ledger_ids"] == ["V800"]
    assert [g["match_id"] for g in forward] == [g["match_id"] for g in backward]


def test_degenerate_fallback_is_deterministic(settings):
    # 节点上限被压到 1 时退化为确定性贪心，两次结果一致
    from app.modules.m5_matching import optimize

    records = split_records()
    candidates = candidate.build(records, settings)
    first, fell_back = optimize.solve_all(candidates, node_cap=1)
    second, _ = optimize.solve_all(list(reversed(candidates)), node_cap=1)
    assert fell_back is True
    assert [item["cand_id"] for item in first] == [item["cand_id"] for item in second]
