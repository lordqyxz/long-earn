"""假设树领域模型（HTR Phase 1）。

HypothesisNode 是树中的一个假设节点；HypothesisTree 管理整棵树的结构和操作。
树操作：add_child / update_evidence / backpropagate_insight / prune_subtree / frontier / best_node。
序列化：serialize / deserialize 支持 JSON 往返。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class NodeStatus(StrEnum):
    """假设节点状态。"""

    PENDING = "pending"
    RUNNING = "running"
    VALIDATED = "validated"
    PRUNED = "pruned"
    MERGED = "merged"
    FAILED = "failed"


@dataclass
class HypothesisNode:
    """假设树中的一个节点。

    Attributes:
        id: 节点唯一标识。
        parent_id: 父节点 ID（根节点为 None）。
        hypothesis: 假设描述（"加动量过滤", "调止损参数"等）。
        direction: 改进方向（收益增强/风险控制/收益稳定性）。
        status: 节点当前状态。
        strategy_ref: 指向策略 dict 的引用 key。
        dev_score: 训练集回测得分（如 sharpe）。
        oos_score: 测试集 OOS 得分。
        backtest_result: 回测结果字典。
        insight: 反思洞察摘要。
        children_ids: 子节点 ID 列表。
        depth: 树深度（根=0）。
        created_at: 创建时间。
    """

    id: str
    parent_id: str | None = None
    hypothesis: str = ""
    direction: str = ""
    status: NodeStatus = NodeStatus.PENDING
    strategy_ref: str = ""
    dev_score: float = 0.0
    oos_score: float | None = None
    backtest_result: dict[str, Any] = field(default_factory=dict)
    insight: str = ""
    children_ids: list[str] = field(default_factory=list)
    depth: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def is_leaf(self) -> bool:
        """是否叶节点（无子节点）。"""
        return len(self.children_ids) == 0

    def is_frontier(self) -> bool:
        """是否前沿节点（pending/running 状态的叶节点）。"""
        return self.is_leaf() and self.status in (NodeStatus.PENDING, NodeStatus.RUNNING)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（JSON 可序列化）。"""
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "hypothesis": self.hypothesis,
            "direction": self.direction,
            "status": str(self.status.value),
            "strategy_ref": self.strategy_ref,
            "dev_score": self.dev_score,
            "oos_score": self.oos_score,
            "backtest_result": self.backtest_result,
            "insight": self.insight,
            "children_ids": self.children_ids,
            "depth": self.depth,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HypothesisNode:
        """从 dict 反序列化。"""
        return cls(
            id=data["id"],
            parent_id=data.get("parent_id"),
            hypothesis=data.get("hypothesis", ""),
            direction=data.get("direction", ""),
            status=NodeStatus(data.get("status", "pending")),
            strategy_ref=data.get("strategy_ref", ""),
            dev_score=data.get("dev_score", 0.0),
            oos_score=data.get("oos_score"),
            backtest_result=data.get("backtest_result", {}),
            insight=data.get("insight", ""),
            children_ids=data.get("children_ids", []),
            depth=data.get("depth", 0),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


class HypothesisTree:
    """假设树 — 持久化研究状态。

    管理 HypothesisNode 的层级关系，支持：
    - add_child: 在父节点下创建子假设
    - update_evidence: 更新节点的实验证据（dev_score / oos_score / insight）
    - backpropagate_insight: 沿路径向上传播洞察
    - prune_subtree: 标记子树为 pruned
    - frontier: 获取可探索的前沿节点
    - best_node: 获取 OOS 得分最高的 validated/merged 节点
    - serialize / deserialize: JSON 往返
    """

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self._nodes: dict[str, HypothesisNode] = {}
        self._root_id: str | None = None
        self.current_best_id: str | None = None

    @property
    def root(self) -> HypothesisNode | None:
        """根节点。"""
        if self._root_id is None:
            return None
        return self._nodes.get(self._root_id)

    def init_root(
        self,
        hypothesis: str = "初始策略",
        direction: str = "",
        strategy_ref: str = "",
    ) -> str:
        """初始化根节点，返回 root_id。"""
        root = HypothesisNode(
            id="root",
            parent_id=None,
            hypothesis=hypothesis,
            direction=direction,
            strategy_ref=strategy_ref,
            depth=0,
            status=NodeStatus.VALIDATED,
        )
        self._nodes["root"] = root
        self._root_id = "root"
        self.current_best_id = "root"
        return root.id

    def add_child(
        self,
        parent_id: str,
        hypothesis: str,
        direction: str = "",
        strategy_ref: str = "",
    ) -> str:
        """在 parent_id 下创建子假设节点，返回新节点 ID。"""
        parent = self._nodes.get(parent_id)
        if parent is None:
            raise ValueError(f"父节点不存在: {parent_id}")

        node_id = f"node_{len(self._nodes)}"
        child = HypothesisNode(
            id=node_id,
            parent_id=parent_id,
            hypothesis=hypothesis,
            direction=direction,
            strategy_ref=strategy_ref,
            depth=parent.depth + 1,
        )
        self._nodes[node_id] = child
        parent.children_ids.append(node_id)
        return node_id

    def update_evidence(  # noqa: PLR0913
        self,
        node_id: str,
        dev_score: float = 0.0,
        oos_score: float | None = None,
        backtest_result: dict[str, Any] | None = None,
        insight: str = "",
        status: NodeStatus = NodeStatus.VALIDATED,
    ) -> None:
        """更新节点的实验证据。"""
        node = self._nodes.get(node_id)
        if node is None:
            raise ValueError(f"节点不存在: {node_id}")
        node.dev_score = dev_score
        if oos_score is not None:
            node.oos_score = oos_score
        if backtest_result is not None:
            node.backtest_result = backtest_result
        if insight:
            node.insight = insight
        node.status = status

    def backpropagate_insight(self, node_id: str) -> None:
        """沿路径向上传播洞察：将子节点的 insight 摘要追加到父节点。"""
        node = self._nodes.get(node_id)
        if node is None or node.parent_id is None:
            return
        parent = self._nodes.get(node.parent_id)
        if parent is None:
            return
        # 简单策略：把子节点 insight 追加到父节点（去重）
        if node.insight and node.insight not in parent.insight:
            if parent.insight:
                parent.insight += f"\n→ {node.insight}"
            else:
                parent.insight = f"→ {node.insight}"
        # 递归向上
        self.backpropagate_insight(parent.id)

    def prune_subtree(self, node_id: str) -> None:
        """标记子树为 pruned。"""
        node = self._nodes.get(node_id)
        if node is None:
            return
        node.status = NodeStatus.PRUNED
        for child_id in node.children_ids:
            self.prune_subtree(child_id)

    def frontier(self) -> list[HypothesisNode]:
        """获取前沿节点（pending/running 状态的叶节点）。"""
        return [
            n
            for n in self._nodes.values()
            if n.is_frontier()
        ]

    def best_node(self) -> HypothesisNode | None:
        """获取 OOS 得分最高的 validated/merged 节点。"""
        candidates = [
            n
            for n in self._nodes.values()
            if n.status in (NodeStatus.VALIDATED, NodeStatus.MERGED)
            and n.oos_score is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda n: n.oos_score or 0.0)

    def get_node(self, node_id: str) -> HypothesisNode | None:
        """按 ID 获取节点。"""
        return self._nodes.get(node_id)

    def all_nodes(self) -> list[HypothesisNode]:
        """获取所有节点。"""
        return list(self._nodes.values())

    @property
    def node_count(self) -> int:
        """节点总数。"""
        return len(self._nodes)

    def serialize(self) -> dict[str, Any]:
        """序列化为 dict（JSON 可序列化）。"""
        return {
            "run_id": self.run_id,
            "root_id": self._root_id,
            "current_best_id": self.current_best_id,
            "nodes": {nid: n.to_dict() for nid, n in self._nodes.items()},
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> HypothesisTree:
        """从 dict 反序列化。"""
        tree = cls(run_id=data.get("run_id", ""))
        tree._root_id = data.get("root_id")
        tree.current_best_id = data.get("current_best_id")
        for nid, ndata in data.get("nodes", {}).items():
            tree._nodes[nid] = HypothesisNode.from_dict(ndata)
        return tree
