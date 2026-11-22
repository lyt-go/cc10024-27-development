import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---------------------------------------------------------------------------
// 清洗后交接专用接口：快照清洗产物发布 → 批次接收正文 → 解析持久化 → 回放
// ---------------------------------------------------------------------------

async function postHandoff(path: string, body?: unknown): Promise<ActionResult> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  })
  return await response.json()
}

export type HandoffRows = { ok: boolean; batch_id: number; count: number; rows: Record<string, unknown>[] }
export type PendingReason = { ok: boolean; batch_id: number; reason: string }
export type BatchReplay = {
  ok: boolean
  replay: {
    批次编号: string
    批次状态: string
    关联快照: string
    页面版本: string
    可读正文: string
    转换依据: { step: string; rule: string }[]
    关联模板: string
    模板版本: string
    接收时间: string
    结构化行: Record<string, unknown>[]
  }
}

export async function cleanSnapshot(
  snapshotId: number,
  readableText: string,
  transformBasis: { step: string; rule: string }[] = [],
): Promise<ActionResult> {
  return postHandoff(`/api/handoffs/snapshots/${snapshotId}/clean`, {
    readable_text: readableText,
    transform_basis: transformBasis,
  })
}

export async function publishHandoff(
  snapshotId: number,
  templateId: number,
): Promise<ActionResult> {
  return postHandoff(`/api/handoffs/snapshots/${snapshotId}/publish`, {
    template_id: templateId,
  })
}

export async function receiveBatchContent(batchId: number): Promise<ActionResult> {
  return postHandoff(`/api/handoffs/batches/${batchId}/receive`)
}

export async function retryReceiveBatch(batchId: number): Promise<ActionResult> {
  return postHandoff(`/api/handoffs/batches/${batchId}/retry-receive`)
}

export async function extractBatch(batchId: number): Promise<ActionResult> {
  return postHandoff(`/api/handoffs/batches/${batchId}/extract`)
}

export async function fetchPendingReason(batchId: number): Promise<PendingReason> {
  const response = await request(`/api/handoffs/batches/${batchId}/pending-reason`)
  if (!response.ok) throw new Error('未完成原因读取失败')
  return await response.json()
}

export async function fetchBatchRows(batchId: number): Promise<HandoffRows> {
  const response = await request(`/api/handoffs/batches/${batchId}/rows`)
  if (!response.ok) throw new Error('可交付行读取失败')
  return await response.json()
}

export async function fetchBatchReplay(batchId: number): Promise<BatchReplay> {
  const response = await request(`/api/handoffs/batches/${batchId}/replay`)
  if (!response.ok) throw new Error('批次回放读取失败')
  return await response.json()
}
