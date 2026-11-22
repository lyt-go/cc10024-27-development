"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.behavior_script import BehaviorScriptService
from app.services.element_selector import ElementSelectorService
from app.services.data_extract import DataExtractService
from app.services.clean_pipeline import CleanPipelineService
from app.services.transform_rule import TransformRuleService
from app.services.quality_guard import QualityGuardService
from app.services.script_schedule import ScriptScheduleService
from app.services.login_mgr import LoginMgrService
from app.services.page_snapshot import PageSnapshotService
from app.services.extract_batch import ExtractBatchService
from app.services.error_recover import ErrorRecoverService
from app.services.page_dedup import PageDedupService
from app.services.field_normalize import FieldNormalizeService
from app.services.batch_queue import BatchQueueService
from app.services.canvas_config import CanvasConfigService
from app.services.export_channel import ExportChannelService
from app.services.run_history import RunHistoryService
from app.services.schema_check import SchemaCheckService
from app.services.content_cache import ContentCacheService

SERVICES: dict[str, BaseModuleService] = {
    "behavior_script": BehaviorScriptService(),
    "element_selector": ElementSelectorService(),
    "data_extract": DataExtractService(),
    "clean_pipeline": CleanPipelineService(),
    "transform_rule": TransformRuleService(),
    "quality_guard": QualityGuardService(),
    "script_schedule": ScriptScheduleService(),
    "login_mgr": LoginMgrService(),
    "page_snapshot": PageSnapshotService(),
    "extract_batch": ExtractBatchService(),
    "error_recover": ErrorRecoverService(),
    "page_dedup": PageDedupService(),
    "field_normalize": FieldNormalizeService(),
    "batch_queue": BatchQueueService(),
    "canvas_config": CanvasConfigService(),
    "export_channel": ExportChannelService(),
    "run_history": RunHistoryService(),
    "schema_check": SchemaCheckService(),
    "content_cache": ContentCacheService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
