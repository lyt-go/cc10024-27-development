"""清洗后交接专用路由：薄路由层，业务判断全部落在 page_snapshot / extract_batch 服务。

覆盖剧本：
- 快照入口生成已清洗快照产物、发布交接并关联实际模板版本；
- 抽取批次接收正文、再次接收、解析正文并持久化结构化行；
- 核对未完成原因、查看可交付行、按批次回放正文/模板/结构化结果。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas import (
    ActionResult,
    CleanSnapshotPayload,
    PublishHandoffPayload,
)
from app.services import service_for

router = APIRouter(prefix="/api/handoffs", tags=["清洗后交接"])


def _require(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.post("/snapshots/{snapshot_id}/clean", response_model=ActionResult)
def clean_snapshot(snapshot_id: int, payload: CleanSnapshotPayload) -> ActionResult:
    service = _require("page_snapshot")
    artifact, message = service.clean_snapshot(
        snapshot_id, payload.readable_text, payload.transform_basis
    )
    if artifact is None:
        return ActionResult(ok=False, message=message)
    snapshot = service.get_entry(snapshot_id)
    return ActionResult(ok=True, message=message, entry=snapshot)


@router.post("/snapshots/{snapshot_id}/publish", response_model=ActionResult)
def publish_handoff(snapshot_id: int, payload: PublishHandoffPayload) -> ActionResult:
    service = _require("page_snapshot")
    batch, message = service.publish_handoff(snapshot_id, payload.template_id)
    return ActionResult(ok=batch is not None, message=message, entry=batch)


@router.post("/batches/{batch_id}/receive", response_model=ActionResult)
def receive_content(batch_id: int) -> ActionResult:
    service = _require("extract_batch")
    entry, message = service.receive_content(batch_id)
    return ActionResult(ok=entry is not None, message=message, entry=entry)


@router.post("/batches/{batch_id}/retry-receive", response_model=ActionResult)
def retry_receive(batch_id: int) -> ActionResult:
    service = _require("extract_batch")
    entry, message = service.retry_receive(batch_id)
    return ActionResult(ok=entry is not None, message=message, entry=entry)


@router.post("/batches/{batch_id}/extract", response_model=ActionResult)
def extract_and_persist(batch_id: int) -> ActionResult:
    service = _require("extract_batch")
    payload, message = service.extract_and_persist(batch_id)
    ok = payload is not None
    return ActionResult(ok=ok, message=message, entry=payload if ok else None)


@router.get("/batches/{batch_id}/pending-reason")
def pending_reason(batch_id: int) -> dict[str, object]:
    service = _require("extract_batch")
    if service.get_entry(batch_id) is None:
        raise HTTPException(status_code=404, detail=f"抽取批次 {batch_id} 不存在或已归档")
    return {"ok": True, "batch_id": batch_id, "reason": service.pending_reason(batch_id)}


@router.get("/batches/{batch_id}/rows")
def deliverable_rows(batch_id: int) -> dict[str, object]:
    service = _require("extract_batch")
    if service.get_entry(batch_id) is None:
        raise HTTPException(status_code=404, detail=f"抽取批次 {batch_id} 不存在或已归档")
    rows = service.deliverable_rows(batch_id)
    return {"ok": True, "batch_id": batch_id, "count": len(rows), "rows": rows}


@router.get("/batches/{batch_id}/replay")
def replay(batch_id: int) -> dict[str, object]:
    service = _require("extract_batch")
    replay_data = service.replay(batch_id)
    if replay_data is None:
        raise HTTPException(status_code=404, detail=f"抽取批次 {batch_id} 不存在或已归档")
    return {"ok": True, "replay": replay_data}
