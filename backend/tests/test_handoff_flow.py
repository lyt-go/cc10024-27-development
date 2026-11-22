"""清洗后交接端到端剧本测试。

剧本（与业务需求一一对应）：
1. 旧页（价格7）清洗并发布交接，发起能提取7的旧批次；
2. 正文接收后、写结构化行之前打断；
3. 打断期间存入结构变更的价格9元页（新档，尚未清洗）；
4. 查看是否已有可交付行（应为空）、核对未完成原因并再次接收（旧批次正文不改写）；
5. 重试旧交接：解析正文、持久化结构化行、确认交付，价格仍为7；
6. 新页清洗并确认、发布为第二个批次并交付，价格为9；
7. 回到归档按两个批次分别回放：正文、模板版本、价格各自独立；
8. 守卫：清洗未完成不能发布；重复确认不改写旧批次；已交付批次只有一份正文/模板/结果。
"""
from __future__ import annotations

import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app import persistence as persist

# 清理测试数据目录（含本特性新增的 handoffs/）
for sub in ["runs", "snapshots", "reports", "exports", "handoffs"]:
    p = persist.data_path(sub, "")
    if os.path.isdir(p):
        for f in os.listdir(p):
            os.remove(os.path.join(p, f))


def _seed_template() -> dict:
    """准备一个已生效的抽取模板，实际模板版本随批次冻结。"""
    from app.services.data_extract import DataExtractService

    svc = DataExtractService()
    entry, missing = svc.create_entry({
        "模板编号": "TPL-PRICE-001",
        "关联脚本": "BEHA-0003",
        "抽取字段": "price",
        "提取方式": "regex",
        "数据处理": "to_number",
        "输出类型": "number",
        "默认值": "0",
        "模板版本": "v1.0",
    })
    assert entry is not None, f"模板登记失败: {missing}"
    result, _ = svc.run_action(entry["id"], "提交校验")
    assert result is not None
    result, _ = svc.run_action(entry["id"], "标记生效")
    assert result is not None and result["status"] == "已生效"
    return result


def _seed_snapshot(code: str, page_version: str) -> dict:
    from app.services.page_snapshot import PageSnapshotService

    svc = PageSnapshotService()
    entry, missing = svc.create_entry({
        "快照编号": code,
        "采集来源": "shop.example.com",
        "页面版本": page_version,
    })
    assert entry is not None, f"快照登记失败: {missing}"
    result, _ = svc.run_action(entry["id"], "标记存档")
    assert result is not None and result["status"] == "已存档"
    return result


def test_full_handoff_scenario():
    from app.store import store
    from app.services.page_snapshot import PageSnapshotService
    from app.services.extract_batch import (
        ExtractBatchService, handoff_path, rows_path,
    )
    from app import persistence as persist_eb

    snapshot_svc = PageSnapshotService()
    batch_svc = ExtractBatchService()
    template = _seed_template()

    # ---- 步骤0：未清洗的快照不能发布交接（清洗未完成不能进入已交付范围） ----
    raw_9 = _seed_snapshot("PAGE-PRICE-9", "v9")
    blocked, block_msg = snapshot_svc.publish_handoff(raw_9["id"], template["id"])
    assert blocked is None and "清洗未完成" in block_msg

    # ---- 步骤1：旧页（价格7）清洗，生成已清洗快照产物 ----
    old_page = _seed_snapshot("PAGE-PRICE-7", "v7")
    old_text = "商品名：示例商品\n价格：7 元\n库存：12"
    transform_basis = [
        {"step": "strip_html", "rule": "移除标签与脚本"},
        {"step": "normalize_price", "rule": "价格：数字 → number"},
    ]
    artifact, msg = snapshot_svc.clean_snapshot(old_page["id"], old_text, transform_basis)
    assert artifact is not None, msg
    assert artifact["可读正文"] == old_text
    assert artifact["转换依据"] == transform_basis
    cleaned = store.find("page_snapshot", old_page["id"])
    assert cleaned["status"] == "已清洗"
    assert os.path.exists(persist.data_path("snapshots", f"cleaned_{cleaned['快照编号']}.json"))

    # 空正文不能通过清洗
    bad_page = _seed_snapshot("PAGE-BAD", "v1")
    none_artifact, none_msg = snapshot_svc.clean_snapshot(bad_page["id"], "   ")
    assert none_artifact is None and "可读正文" in none_msg
    assert store.find("page_snapshot", bad_page["id"])["status"] == "已存档"

    # ---- 步骤2：在快照入口发布交接，关联实际模板版本，生成抽取批次 ----
    batch1, msg = snapshot_svc.publish_handoff(old_page["id"], template["id"])
    assert batch1 is not None, msg
    assert batch1["status"] == "待发布"
    assert batch1["模板版本"] == "v1.0"
    assert batch1["关联模板ID"] == template["id"]
    assert batch1["抽取字段"] == "price"
    b1 = int(batch1["id"])

    # 同一旧交接重复发布：复用批次而不是新建（同一个旧交接收到两次确认的第一层防护）
    again, msg = snapshot_svc.publish_handoff(old_page["id"], template["id"])
    assert again is not None and int(again["id"]) == b1
    assert "复用" in msg
    assert len([r for r in store.rows("extract_batch") if r.get("关联快照ID") == old_page["id"]]) == 1

    # ---- 步骤3：接收正文，随后在写结构化行之前打断 ----
    received, msg = batch_svc.receive_content(b1)
    assert received is not None and received["status"] == "已接收"
    handoff = persist_eb.read_json(
        "handoffs", os.path.basename(handoff_path(b1))
    )
    assert handoff["可读正文"] == old_text
    assert handoff["转换依据"] == transform_basis
    assert handoff["模板版本"] == "v1.0"

    # ---- 步骤4：打断期间结构变更的价格9元页已经入档（此时尚未清洗） ----
    assert store.find("page_snapshot", raw_9["id"])["status"] == "已存档"

    # 打断点：尚无可交付行
    assert batch_svc.deliverable_rows(b1) == []
    reason = batch_svc.pending_reason(b1)
    assert "结构化行尚未写入" in reason

    # 未完成状态下不能确认交付
    none_entry, none_msg = batch_svc.run_action(b1, "确认交付")
    assert none_entry is None and "不能进入已交付范围" in none_msg

    # ---- 步骤5：核对未完成原因并再次接收，重复确认不改写正文 ----
    before_handoff = persist_eb.read_json(
        "handoffs", os.path.basename(handoff_path(b1))
    )
    retried, msg = batch_svc.retry_receive(b1)
    assert retried is not None
    assert "结构化行尚未写入" in msg
    after_handoff = persist_eb.read_json(
        "handoffs", os.path.basename(handoff_path(b1))
    )
    assert before_handoff["可读正文"] == after_handoff["可读正文"]
    assert after_handoff["可读正文"] == old_text
    assert int(retried["接收次数"]) == 2

    # 直接再点一次接收（重复确认）：同样不改写
    dup, msg = batch_svc.receive_content(b1)
    assert dup is not None and "重复接收确认已忽略" in msg
    assert persist_eb.read_json(
        "handoffs", os.path.basename(handoff_path(b1))
    )["可读正文"] == old_text

    # ---- 步骤6：重试旧交接——解析正文并持久化结构化行（提取出7） ----
    payload, msg = batch_svc.extract_and_persist(b1)
    assert payload is not None, msg
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["price"] == 7
    assert payload["rows"][0]["template_version"] == "v1.0"
    assert "price_label_cn" in payload["命中规则"]

    # 重复抽取不改写正式结构化结果
    payload2, msg = batch_svc.extract_and_persist(b1)
    assert payload2 is not None and "不改写" in msg
    assert payload2["rows"][0]["price"] == 7

    # 此时已有可交付行
    rows = batch_svc.deliverable_rows(b1)
    assert len(rows) == 1 and rows[0]["price"] == 7

    # 确认交付：一个批次对应一份正文、一个模板版本、一份结构化结果
    delivered, msg = batch_svc.run_action(b1, "确认交付")
    assert delivered is not None and delivered["status"] == "已交付"
    assert delivered["pending"] is False
    assert batch_svc.pending_reason(b1) == "交接已完成，无未完成事项"

    # 已交付批次不能再次接收
    none_entry, none_msg = batch_svc.retry_receive(b1)
    assert none_entry is None and "已确认交付" in none_msg

    # ---- 步骤7：新页（价格9，结构变更）清洗并确认，另记一批 ----
    new_text = '<article><h1>示例商品</h1><price>9</price><stock>3</stock></article>'
    new_basis = [
        {"step": "strip_html", "rule": "保留语义标签 price"},
        {"step": "extract_price_tag", "rule": "<price>数字 → number"},
    ]
    artifact9, msg = snapshot_svc.clean_snapshot(raw_9["id"], new_text, new_basis)
    assert artifact9 is not None and artifact9["可读正文"] == new_text

    # 重复清洗幂等：不重写产物
    again_artifact, msg = snapshot_svc.clean_snapshot(raw_9["id"], new_text, new_basis)
    assert again_artifact is not None and "幂等" in msg

    batch2, msg = snapshot_svc.publish_handoff(raw_9["id"], template["id"])
    assert batch2 is not None
    b2 = int(batch2["id"])
    assert b2 != b1, "确认的新页面结果应另记一批"

    batch_svc.receive_content(b2)
    payload9, msg = batch_svc.extract_and_persist(b2)
    assert payload9 is not None
    assert payload9["rows"][0]["price"] == 9
    assert "price_tag" in payload9["命中规则"]
    batch_svc.run_action(b2, "确认交付")
    assert store.find("extract_batch", b2)["status"] == "已交付"

    # ---- 步骤8：回到归档，按两个批次分别回放正文、模板与价格 ----
    replay1 = batch_svc.replay(b1)
    replay2 = batch_svc.replay(b2)
    assert replay1 is not None and replay2 is not None

    assert replay1["可读正文"] == old_text
    assert replay1["转换依据"] == transform_basis
    assert replay1["模板版本"] == "v1.0"
    assert replay1["结构化行"][0]["price"] == 7

    assert replay2["可读正文"] == new_text
    assert replay2["转换依据"] == new_basis
    assert replay2["模板版本"] == "v1.0"  # 同一实际模板版本
    assert replay2["结构化行"][0]["price"] == 9

    # 新版本入档后仍能从旧批次回查：两批正文与价格互不串改
    assert replay1["可读正文"] != replay2["可读正文"]
    assert replay1["结构化行"][0]["price"] != replay2["结构化行"][0]["price"]

    # 每批只有一份正文证据文件和一份结构化行文件
    assert os.path.exists(handoff_path(b1)) and os.path.exists(rows_path(b1))
    assert os.path.exists(handoff_path(b2)) and os.path.exists(rows_path(b2))

    # domain_snapshot 汇总
    snap = batch_svc.domain_snapshot()
    assert snap["handoffs_published"] == 2
    assert snap["delivered_batches"] == 2
    assert snap["received_pending_rows"] == 0
    assert snap["deliverable_rows"] == 2
    page_snap = snapshot_svc.domain_snapshot()
    assert page_snap["cleaned_artifacts"] >= 2
    assert page_snap["published_handoffs"] == 2


def test_replay_survives_snapshot_rearchived():
    """快照后续再发生状态变更（回收等），旧批次回放仍读到冻结证据。"""
    from app.store import store
    from app.services.page_snapshot import PageSnapshotService
    from app.services.extract_batch import ExtractBatchService

    snapshot_svc = PageSnapshotService()
    batch_svc = ExtractBatchService()
    template = _seed_template()
    page = _seed_snapshot("PAGE-REPLAY", "v1")
    text = "价格：7"
    snapshot_svc.clean_snapshot(page["id"], text, [{"step": "s", "rule": "r"}])
    batch, _ = snapshot_svc.publish_handoff(page["id"], template["id"])
    bid = int(batch["id"])
    batch_svc.receive_content(bid)
    batch_svc.extract_and_persist(bid)
    batch_svc.run_action(bid, "确认交付")

    # 快照入口继续推进生命周期（回收），不影响已交付批次的回放
    snapshot_svc.run_action(page["id"], "启动回收")
    assert store.find("page_snapshot", page["id"])["status"] == "已回收"
    replay_data = batch_svc.replay(bid)
    assert replay_data["可读正文"] == text
    assert replay_data["结构化行"][0]["price"] == 7
    assert replay_data["批次状态"] == "已交付"


if __name__ == "__main__":
    test_full_handoff_scenario()
    print("PASS: test_full_handoff_scenario")
    test_replay_survives_snapshot_rearchived()
    print("PASS: test_replay_survives_snapshot_rearchived")
    print("\nALL HANDOFF TESTS PASSED")
