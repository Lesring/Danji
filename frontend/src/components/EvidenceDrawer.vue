<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api } from "../api/client";
import { useWorkspaceStore } from "../stores/workspace";
import type { Evidence } from "../types";
import { sourceLabels, labelOf } from "../utils/labels";
import { highlightText } from "../utils/highlight";

const store = useWorkspaceStore();
const evidence = ref<Evidence | null>(null);
const loading = ref(false);

const html = computed(() => highlightText(evidence.value?.raw_text || "", store.excerpt));

watch(
  () => [store.evidenceOpen, store.evidenceId, store.excerpt] as const,
  async ([open, id]) => {
    if (!open || !id) return;
    loading.value = true;
    try {
      evidence.value = await api.evidence(id);
    } finally {
      loading.value = false;
    }
  },
);

function cellHit(rowIndex: number, colIndex: number, value: string) {
  const displayRow = rowIndex + 2;
  const displayCol = colIndex + 1;
  if (store.row === displayRow && store.col === displayCol) return true;
  return Boolean(store.excerpt && value.includes(store.excerpt));
}

function close() {
  store.evidenceOpen = false;
}
</script>

<template>
  <el-drawer :model-value="store.evidenceOpen" title="原始证据" size="460px" @close="close">
    <div v-if="loading" class="muted">正在读取来源…</div>
    <template v-else-if="evidence">
      <div class="tags">
        <el-tag>{{ labelOf(sourceLabels, evidence.type) }}</el-tag>
        <el-tag type="info" v-if="evidence.business_time">{{ evidence.business_time }}</el-tag>
      </div>
      <h3>{{ evidence.title }}</h3>
      <blockquote class="quote" v-if="store.excerpt">命中片段：{{ store.excerpt }}</blockquote>
      <table v-if="evidence.table && evidence.table.columns.length" class="sheet">
        <thead>
          <tr>
            <th v-for="(column, index) in evidence.table.columns" :key="index">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, rowIndex) in evidence.table.rows" :key="rowIndex">
            <td
              v-for="(cell, colIndex) in row"
              :key="colIndex"
              :class="{ hit: cellHit(rowIndex, colIndex, cell) }"
            >
              {{ cell }}
            </td>
          </tr>
        </tbody>
      </table>
      <pre class="evidence-text" v-html="html"></pre>
    </template>
  </el-drawer>
</template>
