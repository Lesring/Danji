<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, errorMessage } from "../api/client";
import DraftCard from "../components/DraftCard.vue";
import type { Drafts, OrderDetail, Risk } from "../types";
import { riskLabels, labelOf, importKindOptions } from "../utils/labels";
import { useWorkspaceStore } from "../stores/workspace";

const route = useRoute();
const router = useRouter();
const store = useWorkspaceStore();
const orderId = computed(() => Number(route.params.id));
const detail = ref<OrderDetail | null>(null);
const drafts = ref<Drafts>({ events: [], risks: [], task_suggestions: [] });
const importKind = ref<"chat" | "file">("chat");
const title = ref("");
const text = ref("");
const businessTime = ref("");
const file = ref<File | null>(null);
const submitting = ref(false);

const textPlaceholder = computed(() =>
  importKind.value === "chat"
    ? "粘贴聊天记录，或上传 TXT / HTML / JSON 导出"
    : "上传 CSV、XLSX、DOCX、PDF、图片或录音。也可以把文档文字贴在这里",
);
const uploadLabel = computed(() => (importKind.value === "chat" ? "上传聊天记录" : "上传文件"));
const uploadAccept = computed(() =>
  importKind.value === "chat"
    ? ".txt,.md,.html,.htm,.json"
    : ".txt,.html,.htm,.json,.csv,.xlsx,.xlsm,.docx,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.gif,.mp3,.wav,.m4a,.ogg",
);

async function reload() {
  detail.value = await api.order(orderId.value);
  drafts.value = await api.drafts(orderId.value);
}

function resolvedType() {
  if (file.value) return importKind.value === "chat" ? "chat" : "file";
  return importKind.value === "chat" ? "chat" : "document";
}

function onFile(upload: { raw?: File }) {
  file.value = upload.raw || null;
}

function onRemove() {
  file.value = null;
}

async function submit() {
  if (!text.value.trim() && !file.value) {
    ElMessage.warning(importKind.value === "chat" ? "先粘贴聊天记录，或上传聊天导出文件" : "先上传文件，或把文档里的文字贴进来");
    return;
  }
  submitting.value = true;
  const sourceType = resolvedType();
  try {
    if (file.value) {
      const form = new FormData();
      form.append("file", file.value);
      form.append("type", sourceType);
      form.append("title", title.value);
      if (businessTime.value) form.append("business_time", businessTime.value);
      drafts.value = await api.uploadSource(orderId.value, form);
    } else {
      drafts.value = await api.createSource(orderId.value, {
        type: sourceType,
        title: title.value,
        text: text.value,
        business_time: businessTime.value,
      });
    }
    await reload();
    ElMessage.success("已生成草案，订单字段尚未改写");
  } catch (error) {
    ElMessage.error(errorMessage(error));
    await reload();
  } finally {
    submitting.value = false;
  }
}

function openRisk(risk: Risk) {
  store.openEvidence(risk.source_id, risk.evidence_excerpt);
}

watch(
  () => store.revision,
  () => {
    void reload();
  },
);

onMounted(reload);
</script>

<template>
  <section class="page" v-if="detail">
    <header class="page-head">
      <div>
        <h1>{{ detail.order.order_no }} · {{ detail.order.customer_name }}</h1>
        <p>
          订单数量 {{ detail.order.quantity }} 件<span v-if="detail.order.delivered_qty">，已交付 {{ detail.order.delivered_qty }} 件，未交 {{ detail.order.quantity - (detail.order.delivered_qty || 0) }} 件</span>，订单交期 {{ detail.order.delivery_date }}，状态 {{ detail.order.status }}。
          在手 {{ detail.supply?.on_hand }}，已占用 {{ detail.supply?.reserved }}，还可承诺 {{ detail.supply?.committable }}。
          本单最多可承诺 {{ detail.supply?.available_qty }}，最早可完工日期 {{ detail.supply?.earliest_completion }}。
        </p>
      </div>
      <el-button @click="router.push('/')">返回工作台</el-button>
    </header>

    <el-alert
      v-if="detail.order.deleted_at"
      type="warning"
      :closable="false"
      title="这张订单在回收站里，恢复后才能继续接入沟通。"
      style="margin-bottom: 12px"
    />

    <div class="panel composer">
      <div class="samples">
        <el-select v-model="importKind" style="width: 160px">
          <el-option v-for="item in importKindOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-input v-model="title" placeholder="来源标题" style="width: 220px" />
      </div>
      <el-input v-model="text" type="textarea" :rows="7" :placeholder="textPlaceholder" />
      <div class="row-actions">
        <el-upload :auto-upload="false" :show-file-list="true" :limit="1" :accept="uploadAccept" :on-change="onFile" :on-remove="onRemove">
          <el-button>{{ uploadLabel }}</el-button>
        </el-upload>
        <el-button type="primary" :loading="submitting" :disabled="!!detail.order.deleted_at" @click="submit">解析为草案</el-button>
      </div>
    </div>

    <div class="section">
      <h2>风险</h2>
      <div class="stack" v-if="drafts.risks.length">
        <article class="card risk" v-for="risk in drafts.risks" :key="risk.id">
          <div class="tags">
            <el-tag type="danger">{{ labelOf(riskLabels, risk.code) }}</el-tag>
          </div>
          <p>{{ risk.message }}</p>
          <el-button v-if="risk.source_id" link type="primary" @click="openRisk(risk)">查看证据</el-button>
        </article>
      </div>
      <div v-else class="empty">当前没有由规则算出的冲突。</div>
    </div>

    <div class="section">
      <h2>事件草案</h2>
      <p class="muted" v-if="drafts.events.some((item) => item.event_type === 'additional_order')">
        有建议为该客户新增商品订单。确认后才会创建，拒绝则不建。
      </p>
      <div class="stack">
        <DraftCard v-for="event in drafts.events" :key="event.id" :event="event" @changed="reload" />
        <div v-if="!drafts.events.length" class="empty">没有待确认事件。</div>
      </div>
    </div>

    <div class="section">
      <h2>任务建议</h2>
      <div class="stack">
        <DraftCard
          v-for="suggestion in drafts.task_suggestions"
          :key="suggestion.id"
          :suggestion="suggestion"
          @changed="reload"
        />
        <div v-if="!drafts.task_suggestions.length" class="empty">没有待确认的任务建议。</div>
      </div>
    </div>
    <p class="muted">「聊天记录」用于对话和导出文件；「文件」用于表格、文档、图片和录音。确认某一条之后才会写入订单或任务。</p>
  </section>
</template>
