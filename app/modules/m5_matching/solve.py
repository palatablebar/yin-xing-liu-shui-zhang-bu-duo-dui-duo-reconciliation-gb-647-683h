"""匹配求解：已确认组保留 → 全局择一 → 组内合计与手续费分摊 → 取舍轨迹。

- confirmed 中的组原样保留，其占用的行退出候选池，重跑不会覆盖；
- 其余候选交给 optimize 全局择一，目标字典序见 optimize 模块 docstring；
- 每条候选都留下决策：chosen / confirmed / dropped（附挡住它的组）。
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

from ...config import dec
from ..m4_candidates import window
from . import fees, optimize


def _match_id(bank_ids: list[str], ledger_ids: list[str]) -> str:
    key = hashlib.sha1(
        f"{','.join(bank_ids)}|{','.join(ledger_ids)}".encode("utf-8")
    ).hexdigest()[:10]
    return f"m-{key}"


def _index(records: list[dict], side: str) -> dict[str, dict]:
    table: dict[str, dict] = {}
    for record in records:
        if record["side"] != side or record["status"] != "valid":
            continue
        table[record["txn_no"]] = {
            "txn_no": record["txn_no"],
            "amount": dec(record["amount"]),
            "fee": dec(record["fee"]),
            "direction": int(record["direction"]),
            "posted_at": window.parse_instant(record["posted_at"]),
        }
    return table


def _candidate_key(bank_ids: list[str], ledger_ids: list[str]) -> tuple:
    return (tuple(sorted(bank_ids)), tuple(sorted(ledger_ids)))


def _group_from_candidate(candidate: dict, bank: dict, ledger: dict, settings) -> dict | None:
    bank_rows = [bank[key] for key in candidate["bank_ids"] if key in bank]
    ledger_rows = [ledger[key] for key in candidate["ledger_ids"] if key in ledger]
    if len(bank_rows) != len(candidate["bank_ids"]) or len(ledger_rows) != len(candidate["ledger_ids"]):
        return None
    bank_sum = sum((row["amount"] for row in bank_rows), Decimal("0.00"))
    ledger_sum = sum((row["amount"] for row in ledger_rows), Decimal("0.00"))
    return {
        "match_id": _match_id(candidate["bank_ids"], candidate["ledger_ids"]),
        "tier": int(candidate["tier"]),
        "bank_ids": list(candidate["bank_ids"]),
        "ledger_ids": list(candidate["ledger_ids"]),
        "bank_sum": bank_sum,
        "ledger_sum": ledger_sum,
        "diff": abs(bank_sum - ledger_sum).quantize(settings.fee_quantum),
        "day_gap": int(candidate["day_gap"]),
        "bank_signed": sum((row["amount"] * row["direction"] for row in bank_rows), Decimal("0.00")),
        "ledger_signed": sum((row["amount"] * row["direction"] for row in ledger_rows), Decimal("0.00")),
        "fee_actual": sum((row["fee"] for row in bank_rows), Decimal("0.00")),
    }


def _group_from_confirmed(group: dict) -> dict:
    return {
        "match_id": group["match_id"],
        "tier": int(group["tier"]),
        "bank_ids": list(group["bank_ids"]),
        "ledger_ids": list(group["ledger_ids"]),
        "bank_sum": dec(group["bank_sum"]),
        "ledger_sum": dec(group["ledger_sum"]),
        "diff": dec(group["diff"]),
        "day_gap": int(group["day_gap"]),
        "bank_signed": dec(group["bank_signed"]),
        "ledger_signed": dec(group["ledger_signed"]),
        "fee_actual": dec(group["fee_actual"]),
    }


def solve(candidates: list[dict], records: list[dict], settings,
          confirmed: list[dict] | None = None) -> dict:
    bank = _index(records, "bank")
    ledger = _index(records, "ledger")
    confirmed = confirmed or []

    confirmed_groups = [_group_from_confirmed(group) for group in confirmed]
    used_bank = {key for group in confirmed_groups for key in group["bank_ids"]}
    used_ledger = {key for group in confirmed_groups for key in group["ledger_ids"]}

    pool = [
        candidate for candidate in candidates
        if not ({*candidate["bank_ids"]} & used_bank or {*candidate["ledger_ids"]} & used_ledger)
    ]
    chosen, degenerate = optimize.solve_all(pool)

    groups: list[dict] = list(confirmed_groups)
    chosen_keys: set[tuple] = set()
    for candidate in chosen:
        group = _group_from_candidate(candidate, bank, ledger, settings)
        if group is not None:
            groups.append(group)
            chosen_keys.add(_candidate_key(candidate["bank_ids"], candidate["ledger_ids"]))

    groups.sort(key=lambda group: (group["tier"], group["match_id"]))
    fee_total = sum((group["fee_actual"] for group in groups), Decimal("0.00"))
    allocations = fees.allocate(
        fee_total,
        [abs(group["bank_signed"]) for group in groups],
        settings.fee_quantum,
        [group["match_id"] for group in groups],
    )
    for group, allocated in zip(groups, allocations):
        group["fee_alloc"] = allocated

    decisions = _decisions(candidates, groups, chosen_keys, confirmed_groups)
    return {"groups": groups, "decisions": decisions, "degenerate": degenerate}


def _decisions(candidates: list[dict], groups: list[dict], chosen_keys: set[tuple],
               confirmed_groups: list[dict]) -> list[dict]:
    row_owner: dict[str, str] = {}
    for group in groups:
        for row_id in list(group["bank_ids"]) + list(group["ledger_ids"]):
            row_owner[row_id] = group["match_id"]

    confirmed_by_key = {
        _candidate_key(group["bank_ids"], group["ledger_ids"]): group["match_id"]
        for group in confirmed_groups
    }

    decisions: list[dict] = []
    seen_keys: set[tuple] = set()
    for candidate in candidates:
        key = _candidate_key(candidate["bank_ids"], candidate["ledger_ids"])
        seen_keys.add(key)
        base = {
            "cand_id": candidate["cand_id"],
            "tier": int(candidate["tier"]),
            "bank_ids": list(candidate["bank_ids"]),
            "ledger_ids": list(candidate["ledger_ids"]),
            "diff": str(candidate["diff"]),
            "day_gap": int(candidate["day_gap"]),
        }
        if key in confirmed_by_key:
            decisions.append({**base, "decision": "confirmed",
                              "match_id": confirmed_by_key[key], "blocked_by": []})
        elif key in chosen_keys:
            decisions.append({**base, "decision": "chosen",
                              "match_id": _match_id(candidate["bank_ids"], candidate["ledger_ids"]),
                              "blocked_by": []})
        else:
            blockers = sorted({
                row_owner[row_id]
                for row_id in list(candidate["bank_ids"]) + list(candidate["ledger_ids"])
                if row_id in row_owner
            })
            decisions.append({**base, "decision": "dropped",
                              "match_id": "", "blocked_by": blockers})

    for group in confirmed_groups:
        key = _candidate_key(group["bank_ids"], group["ledger_ids"])
        if key not in seen_keys:
            decisions.append({
                "cand_id": "",
                "tier": int(group["tier"]),
                "bank_ids": list(group["bank_ids"]),
                "ledger_ids": list(group["ledger_ids"]),
                "diff": str(group["diff"]),
                "day_gap": int(group["day_gap"]),
                "decision": "confirmed",
                "match_id": group["match_id"],
                "blocked_by": [],
            })
    decisions.sort(key=lambda item: (
        item["decision"] == "dropped",
        item["tier"],
        item["cand_id"],
    ))
    return decisions
