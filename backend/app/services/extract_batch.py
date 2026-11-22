"""抽取批次领域服务：承接页面快照的清洗后交接。

职责边界：
- page_snapshot 在快照入口把「已清洗快照产物」发布成抽取批次（待发布），并冻结实际模板版本；
- 本服务负责正文接收（可重复确认但不改写）、未完成原因核对、再次接收、
  调用 data_extract 解析正文并持久化结构化行，以及按批次回放正文/模板/结果；
- 已确认（已交付）批次只能对应一份通过清洗的可读正文、一个模板版本和一份正式结构化结果。
"""
from __future__ import annotations

import os
from typing import Any

from app.store import store
from app.services.base import BaseModuleService, _version_id
from app import persistence as persist


def _batch_code(batch_id: int) -> str:
    """批次业务编号：建批时按固定规则生成，这里用同一规则推导并以仓库值兜底。"""
    entry = store.find("extract_batch", batch_id)
    return str(entry.get("批次编号")) if entry and entry.get("批次编号") else f"EXTR-{batch_id:04d}"


def handoff_path(batch_id: int) -> str:
    """批次交接证据文件：冻结的可读正文、转换依据与模板版本。

    文件名用批次业务编号而非自增 id，内存仓库重建后 id 复用也不会串档。
    """
    return persist.data_path("handoffs", f"batch_{_batch_code(batch_id)}.json")


def rows_path(batch_id: int) -> str:
    """批次正式结构化行文件。"""
    return persist.data_path("handoffs", f"rows_{_batch_code(batch_id)}.json")


class ExtractBatchService(BaseModuleService):
    MODULE = "extract_batch"
    ENTITY = "抽取批次"
    OPERATION_TERM = "交接"
    LIST_FIELDS = [
        "批次编号", "关联快照", "页面版本", "关联模板", "模板版本",
        "抽取字段", "交接时间", "接收次数",
    ]
    REQUIRED_FIELDS = ["批次编号"]
    STATUS_ORDER = ["待发布", "已接收", "已交付"]
    ACTION_RULES = {"接收正文": "已接收", "确认交付": "已交付"}
    NEGATIVE_ACTIONS: list[str] = []

    # ------------------------------------------------------------------
    # 发布流转：由 PageSnapshotService.publish_handoff 调用
    # ------------------------------------------------------------------
    def open_handoff(
        self, snapshot: dict[str, Any], template: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        """把一份已清洗快照产物发布为抽取批次，并冻结模板版本。

        同一快照重复发布时复用既有批次，不重复建批。
        """
        rows = store.rows(self.MODULE)
        snapshot_id = int(snapshot["id"])
        existed = next((r for r in rows if r.get("关联快照ID") == snapshot_id), None)
        if existed is not None:
            return existed, "该快照已存在交接批次，复用既有批次而非重复建批"

        batch_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        entry: dict[str, Any] = {
            "id": batch_id,
            "批次编号": f"EXTR-{batch_id:04d}",
            "关联快照": snapshot.get("快照编号", ""),
            "关联快照ID": snapshot_id,
            "快照版本": snapshot.get("version", ""),
            "页面版本": snapshot.get("页面版本", ""),
            "关联模板": template.get("模板编号", ""),
            "关联模板ID": int(template["id"]),
            "模板版本": template.get("模板版本", ""),
            "模板指纹": template.get("version", ""),
            "抽取字段": template.get("抽取字段", ""),
            "交接时间": persist.now_iso(),
            "接收次数": 0,
            "状态说明": "已清洗快照产物已发布，等待接收正文",
            "status": "待发布",
            "pending": True,
            "abnormal": False,
            "last_action": "发布交接",
        }
        entry["version"] = _version_id(entry)
        entry["updated_at"] = persist.now_iso()
        rows.append(entry)
        self._persist_to_jsonl(entry, action="publish_handoff")
        return entry, "已清洗快照产物已发布到抽取交接"

    # ------------------------------------------------------------------
    # 正文接收
    # ------------------------------------------------------------------
    def receive_content(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        """接收已清洗快照产物中的可读正文到批次。

        重复确认（已接收/已交付后再次接收）只累计确认次数，
        不改写批次冻结的正文、转换依据与模板版本。
        """
        entry = store.find(self.MODULE, batch_id)
        if entry is None:
            return None, f"抽取批次 {batch_id} 不存在或已归档"

        path = handoff_path(batch_id)
        if entry.get("status") in ("已接收", "已交付") and os.path.exists(path):
            entry["接收次数"] = int(entry.get("接收次数", 0)) + 1
            self._persist_to_jsonl(entry, action="receive_duplicate_ignored")
            return entry, "重复接收确认已忽略，批次正文与模板版本保持不变"

        snapshot = store.find("page_snapshot", int(entry["关联快照ID"]))
        if snapshot is None:
            return None, "关联页面快照不存在，无法接收正文"
        artifact = persist.read_json(
            "snapshots", f"cleaned_{snapshot.get('快照编号')}.json"
        )
        if not artifact or not artifact.get("可读正文"):
            return None, "已清洗快照产物缺失或正文为空，无法接收"

        handoff = {
            "批次编号": entry.get("批次编号"),
            "关联快照": entry.get("关联快照"),
            "关联快照ID": entry.get("关联快照ID"),
            "快照版本": entry.get("快照版本"),
            "页面版本": entry.get("页面版本"),
            "可读正文": artifact.get("可读正文"),
            "转换依据": artifact.get("转换依据", []),
            "关联模板": entry.get("关联模板"),
            "关联模板ID": entry.get("关联模板ID"),
            "模板版本": entry.get("模板版本"),
            "模板指纹": entry.get("模板指纹"),
            "received_at": persist.now_iso(),
        }
        persist.write_json(
            "handoffs", os.path.basename(handoff_path(batch_id)), handoff
        )

        entry["status"] = "已接收"
        entry["pending"] = True
        entry["接收次数"] = int(entry.get("接收次数", 0)) + 1
        entry["状态说明"] = "正文已接收，尚未写入结构化行"
        entry["last_action"] = "接收正文"
        entry["updated_at"] = persist.now_iso()
        entry["version"] = _version_id(entry)
        self._persist_to_jsonl(entry, action="接收正文")
        return entry, "清洗后可读正文已接收到批次"

    def retry_receive(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        """正文已接收但结构化行未写入时，核对未完成原因并再次接收。

        再次接收不重写交接证据文件，正文与转换依据保持原值，只记录重试痕迹。
        """
        entry = store.find(self.MODULE, batch_id)
        if entry is None:
            return None, f"抽取批次 {batch_id} 不存在或已归档"
        if entry.get("status") == "已交付":
            return None, "批次已确认交付，不能再次接收"
        if entry.get("status") != "已接收":
            return None, f"批次当前为「{entry.get('status')}」，需先接收正文才能重试"
        if self._has_rows(batch_id):
            return None, "结构化行已写入，重复接收不得改写既有正文"
        if not os.path.exists(handoff_path(batch_id)):
            return None, "交接正文证据缺失，无法再次接收"

        reason = self.pending_reason(batch_id)
        entry["接收次数"] = int(entry.get("接收次数", 0)) + 1
        entry["状态说明"] = f"已再次接收（正文未改写）；未完成原因：{reason}"
        entry["last_action"] = "再次接收"
        entry["updated_at"] = persist.now_iso()
        entry["version"] = _version_id(entry)
        self._persist_to_jsonl(entry, action="retry_receive")
        return entry, f"未完成原因：{reason}；已再次接收，正文保持不变"

    # ------------------------------------------------------------------
    # 结构化行
    # ------------------------------------------------------------------
    def extract_and_persist(
        self, batch_id: int
    ) -> tuple[dict[str, Any] | None, str]:
        """数据抽取解析批次正文并持久化结构化行。

        已写入过结构化行的批次重复抽取时直接读回，不改写正式结果。
        """
        entry = store.find(self.MODULE, batch_id)
        if entry is None:
            return None, f"抽取批次 {batch_id} 不存在或已归档"
        if not os.path.exists(handoff_path(batch_id)):
            return None, "正文尚未接收，不能解析正文"
        if self._has_rows(batch_id):
            existing = persist.read_json(
                "handoffs", os.path.basename(rows_path(batch_id))
            )
            return existing, "结构化行已存在，重复抽取不改写正式结果"

        handoff = persist.read_json(
            "handoffs", os.path.basename(handoff_path(batch_id))
        )
        if not handoff or not handoff.get("可读正文"):
            return None, "交接正文证据缺失，无法解析"

        template = store.find("data_extract", int(entry["关联模板ID"]))
        if template is None or template.get("status") != "已生效":
            return None, "关联抽取模板未处于已生效状态，无法解析正文"

        # 局部导入避免服务间模块级循环依赖
        from app.services.data_extract import DataExtractService

        parsed, rule = DataExtractService().parse_readable(
            template, handoff["可读正文"]
        )
        if not parsed:
            return None, f"正文解析未命中抽取字段（{rule}）"

        payload = {
            "批次编号": entry.get("批次编号"),
            "关联快照": entry.get("关联快照"),
            "页面版本": entry.get("页面版本"),
            "模板编号": template.get("模板编号"),
            "模板版本": handoff.get("模板版本"),
            "模板指纹": handoff.get("模板指纹"),
            "命中规则": rule,
            "抽取时间": persist.now_iso(),
            "rows": parsed,
        }
        persist.write_json(
            "handoffs", os.path.basename(rows_path(batch_id)), payload
        )
        entry["状态说明"] = f"结构化行已写入（{len(parsed)} 行），待确认交付"
        entry["last_action"] = "写入结构化行"
        entry["updated_at"] = persist.now_iso()
        entry["version"] = _version_id(entry)
        self._persist_to_jsonl(entry, action="persist_structured_rows")
        return payload, "正文已解析并持久化结构化行"

    def deliverable_rows(self, batch_id: int) -> list[dict[str, Any]]:
        """查看当前可交付的结构化行；未写入时为空（即尚无可交付行）。"""
        payload = persist.read_json(
            "handoffs", os.path.basename(rows_path(batch_id))
        )
        return list(payload.get("rows", [])) if payload else []

    def _has_rows(self, batch_id: int) -> bool:
        payload = persist.read_json(
            "handoffs", os.path.basename(rows_path(batch_id))
        )
        return bool(payload and payload.get("rows"))

    # ------------------------------------------------------------------
    # 未完成原因与回放
    # ------------------------------------------------------------------
    def pending_reason(self, batch_id: int) -> str:
        """核对批次未能进入已交付范围的具体原因。"""
        entry = store.find(self.MODULE, batch_id)
        if entry is None:
            return f"抽取批次 {batch_id} 不存在或已归档"
        reasons: list[str] = []
        if entry.get("status") == "待发布":
            reasons.append("正文尚未接收")
        if not os.path.exists(handoff_path(batch_id)):
            reasons.append("交接正文证据缺失")
        if not self._has_rows(batch_id):
            reasons.append("结构化行尚未写入")
        template = store.find("data_extract", int(entry.get("关联模板ID", 0)))
        if template is None or template.get("status") != "已生效":
            reasons.append("关联抽取模板未处于已生效状态")
        return "；".join(reasons) if reasons else "交接已完成，无未完成事项"

    def replay(self, batch_id: int) -> dict[str, Any] | None:
        """按批次回放正文、转换依据、模板版本与正式结构化结果。

        证据全部从批次自己的交接文件读取，快照后续入新档不影响旧批次回放。
        """
        entry = store.find(self.MODULE, batch_id)
        if entry is None:
            return None
        handoff = persist.read_json(
            "handoffs", os.path.basename(handoff_path(batch_id))
        ) or {}
        rows_payload = persist.read_json(
            "handoffs", os.path.basename(rows_path(batch_id))
        ) or {}
        return {
            "批次编号": entry.get("批次编号"),
            "批次状态": entry.get("status"),
            "关联快照": entry.get("关联快照"),
            "页面版本": handoff.get("页面版本", entry.get("页面版本")),
            "快照版本": handoff.get("快照版本", entry.get("快照版本")),
            "可读正文": handoff.get("可读正文"),
            "转换依据": handoff.get("转换依据", []),
            "关联模板": handoff.get("关联模板", entry.get("关联模板")),
            "模板版本": handoff.get("模板版本", entry.get("模板版本")),
            "模板指纹": handoff.get("模板指纹", entry.get("模板指纹")),
            "接收时间": handoff.get("received_at"),
            "结构化行": rows_payload.get("rows", []),
            "行抽取时间": rows_payload.get("抽取时间"),
        }

    # ------------------------------------------------------------------
    # 动作守卫与统计
    # ------------------------------------------------------------------
    def run_action(
        self, entry_id: int, action: str
    ) -> tuple[dict[str, Any] | None, str]:
        if action == "接收正文":
            return self.receive_content(entry_id)
        if action == "确认交付" and not self._has_rows(entry_id):
            return None, "结构化行尚未写入，清洗交接未完成，不能进入已交付范围"
        result, message = super().run_action(entry_id, action)
        if result is not None and action == "确认交付":
            result["状态说明"] = "批次已确认交付，正文/模板版本/结构化行已冻结"
            result["pending"] = False
        return result, message

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        base["handoffs_published"] = len(rows)
        base["received_pending_rows"] = sum(
            1 for r in rows
            if r.get("status") == "已接收"
            and not self._has_rows(int(r.get("id", 0)))
        )
        base["delivered_batches"] = sum(
            1 for r in rows if r.get("status") == "已交付"
        )
        row_files = [f for f in persist.list_files("handoffs", ".json")
                     if f.startswith("rows_")]
        base["structured_row_files"] = len(row_files)
        base["deliverable_rows"] = sum(len(self.deliverable_rows(int(r["id"])))
                                       for r in rows)
        return base
