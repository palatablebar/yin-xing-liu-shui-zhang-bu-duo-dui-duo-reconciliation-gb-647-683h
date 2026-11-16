"""匹配服务：求解、落库、查询、确认锁定。

确认语义：复核人确认的组写入 match_confirmations；之后任何重跑
（单步求解、候选刷新后再求解、同输入重新导入）都原样保留这些组，
只有未确认的行重新参与全局择一。
"""
from __future__ import annotations

from decimal import Decimal

from ...errors import DomainError
from ...manifest import utc_now_iso
from . import solve


def _confirmed_groups(store, run_id: str) -> list[dict]:
    confirmed_ids = {item["match_id"] for item in store.confirmations(run_id)}
    return [group for group in store.matches(run_id)
            if group["match_id"] in confirmed_ids]


def payload(store, run: dict | None) -> dict:
    if not run:
        return {"run_id": None, "count": 0, "tiers": {}, "groups": [],
                "fee_alloc_total": "0.00", "fee_actual_total": "0.00",
                "match_gap": "0.00", "decisions": [], "dropped_count": 0,
                "confirmed_count": 0}
    run_id = run["run_id"]
    groups = store.matches(run_id)
    confirmed_ids = {item["match_id"] for item in store.confirmations(run_id)}
    tiers: dict[str, int] = {}
    fee_alloc = Decimal("0.00")
    fee_actual = Decimal("0.00")
    bank_signed = Decimal("0.00")
    ledger_signed = Decimal("0.00")
    for group in groups:
        key = str(group["tier"])
        tiers[key] = tiers.get(key, 0) + 1
        group["confirmed"] = group["match_id"] in confirmed_ids
        fee_alloc += Decimal(group["fee_alloc"])
        fee_actual += Decimal(group["fee_actual"])
        bank_signed += Decimal(group["bank_signed"])
        ledger_signed += Decimal(group["ledger_signed"])
    decisions = store.match_decisions(run_id)
    dropped = [item for item in decisions if item["decision"] == "dropped"]
    return {
        "run_id": run_id,
        "count": len(groups),
        "tiers": tiers,
        "groups": groups,
        "fee_alloc_total": format(fee_alloc, "f"),
        "fee_actual_total": format(fee_actual, "f"),
        "match_gap": format(bank_signed - ledger_signed, "f"),
        "decisions": decisions,
        "dropped_count": len(dropped),
        "confirmed_count": len(confirmed_ids),
    }


def run_matching(store, run: dict | None, settings) -> dict:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 404)
    candidates = store.candidates(run["run_id"])
    if not candidates:
        raise DomainError("E_NO_CANDIDATES", "还没有候选", 409)
    records = store.records(run["run_id"])
    confirmed = _confirmed_groups(store, run["run_id"])
    result = solve.solve(candidates, records, settings, confirmed=confirmed)
    store.replace_match_results(run["run_id"], result["groups"], result["decisions"])
    store.set_status(run["run_id"], "matched")
    return payload(store, store.get_run(run["run_id"]))


def set_confirmation(store, run: dict | None, match_id: str, confirmed: bool) -> dict:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 404)
    known = {group["match_id"] for group in store.matches(run["run_id"])}
    if match_id not in known:
        raise DomainError("E_NO_MATCH", f"匹配组 {match_id} 不存在", 404)
    if confirmed:
        store.confirm_match(run["run_id"], match_id, utc_now_iso())
    else:
        store.unconfirm_match(run["run_id"], match_id)
    return payload(store, store.get_run(run["run_id"]))
