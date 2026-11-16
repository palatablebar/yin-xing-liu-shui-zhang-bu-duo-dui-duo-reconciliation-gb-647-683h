"""全局择一：冲突分量分解 + 分支定界求字典序最优。

取代纯层级贪心：一对一不再无条件先占位，而是所有候选在同一目标下竞争。
目标按字典序（前者优先）：

1. 对平笔数最多（银行行 + 账簿行，直接减少月末未匹配项）；
2. 对平金额最大（逐组 min(银行合计, 账簿合计) 求和）；
3. 总差额最小（不为覆盖率硬凑弱关系）；
4. 总日期差最小；
5. 结构最简单（层级和最小：一对一 < 一对多 < 多对一）；
6. 业务号升序（同额同日多条候选时，统一取业务号小者）。

候选生成规则（方向、容差、窗口）不变，最优解只从既有候选里挑。
求解按「第一个未定的行有哪些候选可覆盖」分支（DLX 式），显式栈不递归；
先用确定性贪心做 incumbent，分量内节点超过 NODE_CAP 时整体退回该贪心，
结果仍可复现。
"""
from __future__ import annotations

from ...config import dec
from . import subset

NODE_CAP = 100_000


def _sort_key(candidate: dict) -> tuple:
    return subset.priority_key(candidate)


def _rows(candidate: dict) -> int:
    return len(candidate["bank_ids"]) + len(candidate["ledger_ids"])


def _amount_cents(candidate: dict) -> int:
    matched = min(dec(candidate["bank_sum"]), dec(candidate["ledger_sum"]))
    return int((matched * 100).to_integral_value())


def _diff_cents(candidate: dict) -> int:
    return int((abs(dec(candidate["diff"])) * 100).to_integral_value())


def _tie_key(candidate: dict) -> tuple:
    return (",".join(candidate["bank_ids"]), ",".join(candidate["ledger_ids"]))


def _score_add(score: list[int], candidate: dict, sign: int) -> None:
    score[0] += sign * _rows(candidate)
    score[1] += sign * _amount_cents(candidate)
    score[2] -= sign * _diff_cents(candidate)
    score[3] -= sign * int(candidate["day_gap"])
    score[4] -= sign * int(candidate["tier"])


def _score_of(candidates: list[dict]) -> tuple:
    score = [0, 0, 0, 0, 0]
    for candidate in candidates:
        _score_add(score, candidate, 1)
    return tuple(score)


def decompose(candidates: list[dict]) -> list[list[dict]]:
    """按共享行把候选分成互不连通的连通分量，分量之间互不影响。"""
    row_owner: dict[str, int] = {}
    parent = list(range(len(candidates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    for index, candidate in enumerate(candidates):
        for row_id in list(candidate["bank_ids"]) + list(candidate["ledger_ids"]):
            if row_id in row_owner:
                union(index, row_owner[row_id])
            else:
                row_owner[row_id] = index

    components: dict[int, list[dict]] = {}
    for index, candidate in enumerate(candidates):
        components.setdefault(find(index), []).append(candidate)
    return [components[key] for key in sorted(components)]


def _solve_component(candidates: list[dict], node_cap: int) -> tuple[list[dict], bool]:
    """单分量分支定界；超节点上限退化为确定性贪心。"""
    ordered = sorted(candidates, key=_sort_key)
    greedy = subset.choose_disjoint(ordered)
    if len(ordered) <= 1:
        return greedy, False

    rowsets = [set(c["bank_ids"]) | set(c["ledger_ids"]) for c in ordered]
    row_to_cands: dict[str, list[int]] = {}
    for index, candidate in enumerate(ordered):
        for row_id in rowsets[index]:
            row_to_cands.setdefault(row_id, []).append(index)
    all_rows = sorted(row_to_cands)
    total_rows = len(all_rows)

    best_score = _score_of(greedy)
    best_ties = sorted(_tie_key(item) for item in greedy)
    best_chosen = list(greedy)

    used: set[str] = set()
    decided: set[str] = set()
    current: list[int] = []
    score = [0, 0, 0, 0, 0]

    def open_row() -> str | None:
        for row_id in all_rows:
            if row_id in used or row_id in decided:
                continue
            for index in row_to_cands[row_id]:
                if rowsets[index].isdisjoint(used):
                    return row_id
        return None

    def evaluate() -> None:
        nonlocal best_score, best_ties, best_chosen
        final = tuple(score)
        if final < best_score:
            return
        ties = sorted(_tie_key(ordered[index]) for index in current)
        if final > best_score or ties < best_ties:
            best_score = final
            best_ties = ties
            best_chosen = [ordered[index] for index in current]

    def make_frame() -> tuple[str, list | None]:
        """返回 (pruned|leaf|ok, 帧)。帧 = [行, 分支列表, 游标, 已应用候选, 行已置空]。"""
        if score[0] + (total_rows - len(used) - len(decided)) < best_score[0]:
            return "pruned", None
        row_id = open_row()
        if row_id is None:
            return "leaf", None
        branches = [index for index in row_to_cands[row_id]
                    if rowsets[index].isdisjoint(used)]
        branches.append(None)  # 该行业务上留空的分支
        return "ok", [row_id, branches, 0, None, False]

    degenerate = False
    nodes = 0
    stack: list[list] = []
    status, frame = make_frame()
    if status == "leaf":
        evaluate()
    elif status == "ok":
        stack.append(frame)

    while stack:
        nodes += 1
        if nodes > node_cap:
            degenerate = True
            break
        frame = stack[-1]
        row_id, branches, pos, applied, row_decided = frame
        if applied is not None:
            _score_add(score, ordered[applied], -1)
            used.difference_update(rowsets[applied])
            current.pop()
            frame[3] = None
        if row_decided:
            decided.discard(row_id)
            frame[4] = False
        if pos >= len(branches):
            stack.pop()
            continue
        branch = branches[pos]
        frame[2] += 1
        if branch is None:
            decided.add(row_id)
            frame[4] = True
        else:
            used.update(rowsets[branch])
            current.append(branch)
            _score_add(score, ordered[branch], 1)
            frame[3] = branch
        status, child = make_frame()
        if status == "leaf":
            evaluate()
        elif status == "ok":
            stack.append(child)

    if degenerate:
        return greedy, True
    return best_chosen, False


def solve_all(candidates: list[dict], node_cap: int = NODE_CAP) -> tuple[list[dict], bool]:
    """全量求解：分量分解后各自求最优再合并；返回 (选中候选, 是否触发退化)。"""
    chosen: list[dict] = []
    degenerate = False
    for component in decompose(candidates):
        part, fell_back = _solve_component(component, node_cap)
        chosen.extend(part)
        degenerate = degenerate or fell_back
    chosen.sort(key=_sort_key)
    return chosen, degenerate
