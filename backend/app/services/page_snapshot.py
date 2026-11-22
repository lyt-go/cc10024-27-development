"""页面快照领域服务：页面DOM快照的采集与存储，消费login_mgr凭证和canvas_config配置。

清洗后交接扩展：
- 「确认清洗」生成已清洗快照产物（可读正文 + 转换依据），重复清洗为幂等操作；
- publish_handoff 在快照入口把产物发布给抽取交接，关联实际模板版本，
  由 extract_batch 承接正文接收与结构化行持久化。
"""
from __future__ import annotations

import os
from typing import Any

from app.store import store
from app.services.base import BaseModuleService, register_hook
from app import persistence as persist


def cleaned_artifact_path(snapshot_code: str) -> str:
    return persist.data_path("snapshots", f"cleaned_{snapshot_code}.json")


class PageSnapshotService(BaseModuleService):
    MODULE = "page_snapshot"
    ENTITY = "页面快照"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["快照编号", "采集来源", "页面版本", "DOM摘要", "快照时间", "快照类型", "数据量", "回收时间"]
    REQUIRED_FIELDS = ["快照编号", "采集来源", "页面版本"]
    STATUS_ORDER = ["草稿", "已存档", "已清洗", "已回收"]
    ACTION_RULES = {"标记存档": "已存档", "确认清洗": "已清洗", "启动回收": "已回收"}
    NEGATIVE_ACTIONS = []

    def capture_snapshot(self, page_url: str, dom_hash: str, raw_dom: str) -> dict[str, Any]:
        """采集页面快照并存入snapshots/目录。"""
        snapshot = {
            "page_url": page_url,
            "dom_hash": dom_hash,
            "raw_dom_length": len(raw_dom),
            "timestamp": persist.now_iso(),
            "dom_preview": raw_dom[:500],
        }
        persist.write_json("snapshots", f"page_{dom_hash}.json", snapshot)
        return snapshot

    def clean_snapshot(
        self,
        entry_id: int,
        readable_text: str,
        transform_basis: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """生成已清洗快照产物并把快照推进到「已清洗」。

        产物包含可读正文与转换依据；重复清洗为幂等操作：
        已存在产物且快照已清洗时直接读回，不重复写、不改版本。
        未通过清洗（正文为空）不能进入已清洗状态。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if not str(readable_text or "").strip():
            return None, "清洗未产出可读正文，不能进入已清洗状态"

        snapshot_code = str(entry.get("快照编号"))
        path = cleaned_artifact_path(snapshot_code)
        if entry.get("status") == "已清洗" and os.path.exists(path):
            artifact = persist.read_json("snapshots", f"cleaned_{snapshot_code}.json")
            return artifact, "已清洗快照产物已存在，重复清洗保持幂等"

        artifact = {
            "快照编号": snapshot_code,
            "snapshot_id": entry_id,
            "页面版本": entry.get("页面版本"),
            "快照版本": entry.get("version"),
            "可读正文": readable_text,
            "转换依据": transform_basis or [],
            "cleaned_at": persist.now_iso(),
        }
        persist.write_json("snapshots", f"cleaned_{snapshot_code}.json", artifact)

        result, message = self.run_action(entry_id, "确认清洗")
        if result is None:
            return None, message
        result["cleaned_artifact"] = f"snapshots/cleaned_{snapshot_code}.json"
        return artifact, "已清洗快照产物已生成"

    def publish_handoff(
        self, snapshot_id: int, template_id: int
    ) -> tuple[dict[str, Any] | None, str]:
        """在快照入口推进已清洗快照产物的发布流转，关联实际模板版本。

        只有通过清洗（已清洗 + 产物正文可读）的快照才能进入抽取交接；
        同一快照重复发布复用同一批次，不重复建批。
        """
        snapshot = store.find(self.MODULE, snapshot_id)
        if snapshot is None:
            return None, f"{self.ENTITY} {snapshot_id} 不存在或已归档"
        if snapshot.get("status") != "已清洗":
            return None, "清洗未完成，快照不能发布到抽取交接"
        artifact = persist.read_json(
            "snapshots", f"cleaned_{snapshot.get('快照编号')}.json"
        )
        if not artifact or not str(artifact.get("可读正文") or "").strip():
            return None, "已清洗快照产物缺失或正文为空，不能发布"

        template = store.find("data_extract", template_id)
        if template is None:
            return None, f"抽取模板 {template_id} 不存在或已归档"
        if template.get("status") != "已生效":
            return None, "关联抽取模板尚未标记生效，不能发布交接"

        # 局部导入：服务注册阶段 extract_batch 可能尚未初始化
        from app.services.extract_batch import ExtractBatchService

        batch, message = ExtractBatchService().open_handoff(snapshot, template)
        self._persist_to_jsonl(snapshot, action="publish_handoff")
        return batch, message

    def run_action(
        self, entry_id: int, action: str
    ) -> tuple[dict[str, Any] | None, str]:
        if action == "确认清洗":
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            if entry.get("status") not in ("草稿", "已存档"):
                return None, f"当前状态「{entry.get('status')}」不能执行确认清洗"
        return super().run_action(entry_id, action)

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        snap_files = persist.list_files("snapshots", ".json")
        base["snapshot_files"] = len([f for f in snap_files if f.startswith("page_")])
        base["cleaned_artifacts"] = len(
            [f for f in snap_files if f.startswith("cleaned_")]
        )
        # 关联的登录凭证
        credentials = store.rows("login_mgr")
        snaps = store.rows(self.MODULE)
        sites = {r.get("采集来源") for r in snaps}
        base["sites_with_credentials"] = sum(1 for c in credentials if c.get("目标站点") in sites)
        # 已发布到抽取交接的快照数
        batches = store.rows("extract_batch")
        base["published_handoffs"] = len({b.get("关联快照ID") for b in batches})
        return base
