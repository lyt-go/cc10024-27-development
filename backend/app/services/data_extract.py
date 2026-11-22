"""数据抽取领域服务：消费behavior_script脚本和element_selector锚点，执行字段抽取。"""
from __future__ import annotations

import re
from typing import Any

from app.store import store
from app.services.base import BaseModuleService, register_hook
from app import persistence as persist


# 价格抽取规则按页面结构登记，新旧版本入档后旧批次仍能按其命中规则回查转换依据。
PRICE_RULES: list[dict[str, str]] = [
    {"rule_id": "price_label_cn", "pattern": r"价格[：:]\s*([0-9]+(?:\.[0-9]+)?)",
     "applies_to": "旧版标签结构"},
    {"rule_id": "price_tag", "pattern": r"<price[^>]*>\s*([0-9]+(?:\.[0-9]+)?)",
     "applies_to": "新版price标签结构"},
    {"rule_id": "price_json", "pattern": r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?',
     "applies_to": "JSON内嵌结构"},
]


class DataExtractService(BaseModuleService):
    MODULE = "data_extract"
    ENTITY = "抽取模板"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["模板编号", "关联脚本", "抽取字段", "提取方式", "数据处理", "输出类型", "默认值", "模板版本"]
    REQUIRED_FIELDS = ["模板编号", "关联脚本", "抽取字段"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用模板": "已停用"}
    NEGATIVE_ACTIONS = ["停用模板"]

    def extract_fields_for_script(self, script_id: str) -> list[dict[str, Any]]:
        """根据脚本编号查询所有关联的抽取模板并返回字段映射。"""
        templates = store.rows(self.MODULE)
        linked = [t for t in templates if t.get("关联脚本") == script_id and t.get("status") == "已生效"]
        return [{"field": t.get("抽取字段"), "method": t.get("提取方式"), "output_type": t.get("输出类型")} for t in linked]

    def parse_readable(
        self, template: dict[str, Any], readable_text: str
    ) -> tuple[list[dict[str, Any]], str]:
        """用实际模板版本解析清洗后可读正文，返回结构化行与命中规则。

        同一份模板同时支持旧版标签结构（价格：7）与结构变更后的price标签（<price>9），
        解析结果记录命中规则，作为转换依据的一部分随批次冻结、可回放。
        """
        field = str(template.get("抽取字段") or "price")
        rows: list[dict[str, Any]] = []
        hit_rule = ""
        for rule in PRICE_RULES:
            matches = re.findall(rule["pattern"], readable_text)
            if matches:
                hit_rule = f"{rule['rule_id']}（{rule['applies_to']}）"
                for raw in matches:
                    value: Any = float(raw) if "." in raw else int(raw)
                    rows.append({
                        field: value,
                        "template_id": template.get("模板编号"),
                        "template_version": template.get("模板版本"),
                        "rule_id": rule["rule_id"],
                    })
                break
        return rows, hit_rule

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        templates = store.rows(self.MODULE)
        # 关联的元素锚点统计
        selectors = store.rows("element_selector")
        base["templates_with_selectors"] = sum(
            1 for t in templates if any(
                s.get("关联脚本") == t.get("关联脚本") for s in selectors))
        # 关联的清洗管道
        pipelines = store.rows("clean_pipeline")
        base["templates_in_pipeline"] = sum(
            1 for t in templates if any(
                p.get("输入源") and t.get("关联脚本") in str(p.get("输入源", "")) for p in pipelines))
        return base