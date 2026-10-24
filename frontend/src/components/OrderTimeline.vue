<script setup lang="ts">
import type { BusinessEvent } from "../types";
import { eventLabels, fieldLabels, labelOf } from "../utils/labels";
import { useWorkspaceStore } from "../stores/workspace";

defineProps<{ events: BusinessEvent[] }>();
const store = useWorkspaceStore();

function open(event: BusinessEvent) {
  store.openEvidence(event.source_id, event.evidence_excerpt, event.evidence_row, event.evidence_col);
}
</script>

<template>
  <ol class="timeline" v-if="events.length">
    <li v-for="event in events" :key="event.id" :class="{ draft: event.status === 'draft' }">
      <div class="card-top">
        <strong>{{ labelOf(eventLabels, event.event_type) }}</strong>
        <span class="status" :data-status="event.status === 'draft' ? '待确认' : event.status === 'confirmed' ? '已确认' : '已关闭'">
          {{ event.status === "draft" ? "待确认" : event.status === "confirmed" ? "已确认" : "已拒绝" }}
        </span>
      </div>
      <div class="muted">{{ event.proposal_origin }} · {{ event.source_title }}</div>
      <div class="change" v-if="event.field_name && event.proposed_value">
        {{ labelOf(fieldLabels, event.field_name) }}
        {{ event.current_value || "空" }} → <em>{{ event.proposed_value }}</em>
      </div>
      <p>{{ event.explanation }}</p>
      <el-button link type="primary" @click="open(event)">查看证据</el-button>
    </li>
  </ol>
  <div v-else class="empty">还没有从沟通里抽出业务事件。</div>
</template>
