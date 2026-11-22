<template>
  <section class="page" data-module="handoff-console">
    <header class="page-head">
      <div>
        <h2>清洗后交接台</h2>
        <p class="page-desc">
          在快照入口推进已清洗快照产物的发布流转，关联实际模板版本；抽取批次接收可读正文、
          解析并持久化结构化行，已交付批次可按批回放正文、模板与正式结果。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn ghost" type="button" @click="reloadAll">刷新全部</button>
      </div>
    </header>

    <p v-if="message" class="status-legend" :class="{ 'error-text': !lastOk }">{{ message }}</p>

    <div class="handoff-grid">
      <!-- 左栏：快照入口（清洗产物 + 发布交接） -->
      <article class="handoff-panel">
        <h3>快照入口：清洗与发布</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>快照编号</th>
              <th>页面版本</th>
              <th>当前状态</th>
              <th>交接操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="snap in snapshots" :key="String(snap.id)" :data-status="String(snap.status)">
              <td>{{ snap['快照编号'] }}</td>
              <td>{{ snap['页面版本'] ?? '—' }}</td>
              <td>{{ snap.status }}</td>
              <td class="row-actions">
                <button
                  v-if="snap.status === '草稿' || snap.status === '已存档'"
                  class="link"
                  type="button"
                  :data-testid="`clean-${snap.id}`"
                  @click="openClean(snap)"
                >
                  生成已清洗产物
                </button>
                <span v-else class="muted">产物已就绪</span>
                <button
                  v-if="snap.status === '已清洗'"
                  class="link"
                  type="button"
                  :data-testid="`publish-${snap.id}`"
                  @click="openPublish(snap)"
                >
                  发布到抽取交接
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </article>

      <!-- 右栏：抽取批次（接收 / 再接收 / 解析 / 交付 / 回放） -->
      <article class="handoff-panel">
        <h3>抽取批次：接收、结构化与交付</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>批次编号</th>
              <th>关联快照</th>
              <th>模板版本</th>
              <th>批次状态</th>
              <th>交接操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="batch in batches" :key="String(batch.id)" :data-status="String(batch.status)">
              <td>{{ batch['批次编号'] }}</td>
              <td>{{ batch['关联快照'] }}</td>
              <td>{{ batch['模板版本'] ?? '—' }}</td>
              <td>{{ batch.status }}</td>
              <td class="row-actions">
                <button
                  v-if="batch.status === '待发布'"
                  class="link"
                  type="button"
                  :data-testid="`receive-${batch.id}`"
                  @click="onReceive(batch.id)"
                >
                  接收正文
                </button>
                <button
                  v-if="batch.status === '已接收'"
                  class="link"
                  type="button"
                  :data-testid="`retry-${batch.id}`"
                  @click="onRetryReceive(batch.id)"
                >
                  核对原因并再次接收
                </button>
                <button
                  v-if="batch.status === '已接收'"
                  class="link"
                  type="button"
                  :data-testid="`extract-${batch.id}`"
                  @click="onExtract(batch.id)"
                >
                  解析正文并写结构化行
                </button>
                <button
                  v-if="batch.status === '已接收'"
                  class="link"
                  type="button"
                  :data-testid="`deliver-${batch.id}`"
                  @click="onDeliver(batch.id)"
                >
                  确认交付
                </button>
                <button
                  class="link"
                  type="button"
                  :data-testid="`rows-${batch.id}`"
                  @click="onShowRows(batch.id)"
                >
                  可交付行
                </button>
                <button
                  class="link"
                  type="button"
                  :data-testid="`replay-${batch.id}`"
                  @click="onReplay(batch.id)"
                >
                  回放
                </button>
              </td>
            </tr>
            <tr v-if="!batches.length">
              <td colspan="5" class="empty-state">暂无交接批次，请先在左侧发布已清洗快照</td>
            </tr>
          </tbody>
        </table>
      </article>
    </div>

    <!-- 清洗产物弹窗 -->
    <div v-if="cleanTarget" class="modal-backdrop" data-testid="clean-modal">
      <form class="modal" @submit.prevent="submitClean">
        <h3>生成已清洗快照产物 · {{ cleanTarget['快照编号'] }}</h3>
        <label class="create-field">
          <span>可读正文（清洗后）</span>
          <textarea v-model="cleanForm.text" data-testid="clean-text" rows="5" required />
        </label>
        <label class="create-field">
          <span>转换依据（每行一条：步骤=规则）</span>
          <textarea v-model="cleanForm.basis" data-testid="clean-basis" rows="3"
            placeholder="strip_html=移除标签&#10;normalize_price=价格转数字" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="cleanTarget = null">取消</button>
          <button class="btn primary" data-testid="submit-clean" type="submit">确认清洗</button>
        </div>
      </form>
    </div>

    <!-- 发布交接弹窗：关联实际模板版本 -->
    <div v-if="publishTarget" class="modal-backdrop" data-testid="publish-modal">
      <form class="modal" @submit.prevent="submitPublish">
        <h3>发布交接 · {{ publishTarget['快照编号'] }}</h3>
        <label class="create-field">
          <span>关联抽取模板（实际模板版本随批次冻结）</span>
          <select v-model.number="publishForm.templateId" data-testid="publish-template" required>
            <option v-for="tpl in activeTemplates" :key="String(tpl.id)" :value="Number(tpl.id)">
              {{ tpl['模板编号'] }} · 版本 {{ tpl['模板版本'] ?? '—' }}
            </option>
          </select>
        </label>
        <p v-if="!activeTemplates.length" class="error-text">没有已生效的抽取模板，请先在「数据抽取」模块标记生效</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="publishTarget = null">取消</button>
          <button class="btn primary" data-testid="submit-publish" type="submit"
            :disabled="!activeTemplates.length">发布</button>
        </div>
      </form>
    </div>

    <!-- 回放/可交付行抽屉 -->
    <div v-if="replayData" class="modal-backdrop" data-testid="replay-modal">
      <div class="modal">
        <h3>批次回放 · {{ replayData.replay['批次编号'] }}（{{ replayData.replay['批次状态'] }}）</h3>
        <dl class="replay-list">
          <dt>关联快照 / 页面版本</dt>
          <dd>{{ replayData.replay['关联快照'] }} / {{ replayData.replay['页面版本'] }}</dd>
          <dt>模板 / 模板版本</dt>
          <dd>{{ replayData.replay['关联模板'] }} / {{ replayData.replay['模板版本'] }}</dd>
          <dt>可读正文</dt>
          <dd><pre>{{ replayData.replay['可读正文'] }}</pre></dd>
          <dt>转换依据</dt>
          <dd>
            <ul class="basis-list">
              <li v-for="(item, idx) in replayData.replay['转换依据']" :key="idx">
                {{ item.step }}：{{ item.rule }}
              </li>
              <li v-if="!replayData.replay['转换依据'].length" class="muted">无</li>
            </ul>
          </dd>
          <dt>正式结构化行</dt>
          <dd>
            <table v-if="replayData.replay['结构化行'].length" class="data-table">
              <thead>
                <tr>
                  <th v-for="(value, key) in replayData.replay['结构化行'][0]" :key="String(key)">{{ key }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in replayData.replay['结构化行']" :key="idx">
                  <td v-for="(value, key) in row" :key="String(key)">{{ value }}</td>
                </tr>
              </tbody>
            </table>
            <span v-else class="muted">尚未写入结构化行（不在已交付范围内）</span>
          </dd>
        </dl>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="replayData = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  cleanSnapshot,
  extractBatch,
  fetchBatchReplay,
  fetchBatchRows,
  fetchPendingReason,
  listEntries,
  publishHandoff,
  receiveBatchContent,
  retryReceiveBatch,
  runAction as applyAction,
  type BatchReplay,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const snapshots = ref<EntryRow[]>([])
const batches = ref<EntryRow[]>([])
const activeTemplates = ref<EntryRow[]>([])
const message = ref('')
const lastOk = ref(true)

const cleanTarget = ref<EntryRow | null>(null)
const cleanForm = ref({ text: '', basis: '' })
const publishTarget = ref<EntryRow | null>(null)
const publishForm = ref({ templateId: 0 })
const replayData = ref<BatchReplay | null>(null)

function notify(text: string, ok = true) {
  message.value = text
  lastOk.value = ok
}

async function reloadAll() {
  try {
    const [snapPayload, batchPayload, tplPayload] = await Promise.all([
      listEntries('page_snapshot', {}),
      listEntries('extract_batch', {}),
      listEntries('data_extract', { status: '已生效' }),
    ])
    snapshots.value = snapPayload.items
    batches.value = batchPayload.items
    activeTemplates.value = tplPayload.items
  } catch (error) {
    notify(error instanceof Error ? error.message : '交接台数据读取失败', false)
  }
}

function openClean(snap: EntryRow) {
  cleanTarget.value = snap
  cleanForm.value = { text: '', basis: '' }
}

function parseBasis(raw: string): { step: string; rule: string }[] {
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [step, ...rest] = line.split('=')
      return { step: step.trim(), rule: rest.join('=').trim() }
    })
}

async function submitClean() {
  if (!cleanTarget.value) return
  const result = await cleanSnapshot(
    Number(cleanTarget.value.id),
    cleanForm.value.text,
    parseBasis(cleanForm.value.basis),
  )
  notify(result.message, result.ok)
  if (result.ok) cleanTarget.value = null
  await reloadAll()
}

function openPublish(snap: EntryRow) {
  publishTarget.value = snap
  publishForm.value = { templateId: Number(activeTemplates.value[0]?.id ?? 0) }
}

async function submitPublish() {
  if (!publishTarget.value) return
  const result = await publishHandoff(
    Number(publishTarget.value.id),
    publishForm.value.templateId,
  )
  notify(result.message, result.ok)
  if (result.ok) publishTarget.value = null
  await reloadAll()
}

async function onReceive(batchId: number) {
  const result = await receiveBatchContent(batchId)
  notify(result.message, result.ok)
  await reloadAll()
}

async function onRetryReceive(batchId: number) {
  const reason = await fetchPendingReason(batchId)
  const result = await retryReceiveBatch(batchId)
  notify(`未完成原因：${reason.reason} ｜ ${result.message}`, result.ok)
  await reloadAll()
}

async function onExtract(batchId: number) {
  const result = await extractBatch(batchId)
  notify(result.message, result.ok)
  await reloadAll()
}

async function onDeliver(batchId: number) {
  const result = await applyAction('extract_batch', batchId, '确认交付')
  notify(result.message, result.ok)
  await reloadAll()
}

async function onShowRows(batchId: number) {
  const payload = await fetchBatchRows(batchId)
  notify(
    payload.count
      ? `批次 ${batchId} 现有 ${payload.count} 行可交付结构化行`
      : `批次 ${batchId} 尚无可交付结构化行（清洗交接未完成）`,
    true,
  )
}

async function onReplay(batchId: number) {
  replayData.value = await fetchBatchReplay(batchId)
}

onMounted(reloadAll)
</script>

<style scoped>
.handoff-grid {
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  gap: 16px;
}
.handoff-panel {
  background: var(--card-bg, #fff);
  border: 1px solid var(--border-color, #e3e6eb);
  border-radius: 10px;
  padding: 14px;
}
.replay-list dt {
  font-weight: 600;
  margin-top: 10px;
}
.replay-list dd {
  margin: 4px 0 0;
}
.replay-list pre {
  white-space: pre-wrap;
  background: #f6f7f9;
  padding: 8px;
  border-radius: 6px;
}
.basis-list {
  padding-left: 18px;
}
.muted {
  color: #8a9099;
}
@media (max-width: 1100px) {
  .handoff-grid {
    grid-template-columns: 1fr;
  }
}
</style>
