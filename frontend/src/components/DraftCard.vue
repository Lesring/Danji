<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, errorMessage } from "../api/client";
import type { BusinessEvent, TaskSuggestion } from "../types";
import { actionLabels, eventLabels, fieldLabels, labelOf } from "../utils/labels";
import { useWorkspaceStore } from "../stores/workspace";

const props = defineProps<{
  event?: BusinessEvent;
  suggestion?: TaskSuggestion;
}>();
const emit = defineEmits<{ changed: [] }>();
const store = useWorkspaceStore();

const applyToOrder = ref(false);
const title = ref(props.suggestion?.title || "");
const owner = ref(props.suggestion?.owner || "");
const dueDate = ref(props.suggestion?.due_date || "");
const busy = ref(false);

watch(
  () => props.suggestion,
  (value) => {
    if (!value) return;
    title.value = value.title;
    owner.value = value.owner;
    dueDate.value = value.due_date || "";
  },
);

const additional = computed(() => {
  if (props.event?.event_type !== "additional_order" || !props.event.proposed_value) return null;
  try {
    const data = JSON.parse(props.event.proposed_value) as {
      product?: string;
      quantity?: number;
      delivery_date?: string;
    };
    if (!data?.product) return null;
    return data;
  } catch {
    return null;
  }
});

const canApply =
  !!props.event &&
  props.event.event_type !== "commitment" &&
  props.event.event_type !== "partial_delivery" &&
  props.event.event_type !== "additional_order" &&
  props.event.proposal_origin !== "销售承诺" &&
  (props.event.field_name === "quantity" || props.event.field_name === "delivery_date");

const writesSupply =
  !!props.event &&
  (props.event.event_type === "inventory_fact" || props.event.event_type === "production_constraint");

async function confirm() {
  busy.value = true;
  try {
    if (props.event) {
      const createdNo = (await api.confirm(props.event.id, {
        kind: "event",
        apply_to_order: canApply && applyToOrder.value,
      })) as { created_order_no?: string };
      ElMessage.success(
        props.event.event_type === "additional_order"
          ? createdNo.created_order_no
            ? `已为该客户新增商品订单 ${createdNo.created_order_no}`
            : "已确认，未重复新建订单"
          : props.event.event_type === "partial_delivery"
            ? "已记录已交付数量，订单数量未改"
            : writesSupply
              ? "已更新供应，并重算了这个商品下的未关闭订单"
              : canApply && applyToOrder.value
                ? "已写入订单"
                : "已记录，未改写订单",
      );
    } else if (props.suggestion) {
      await api.confirm(props.suggestion.id, {
        kind: "task",
        title: title.value,
        owner: owner.value,
        due_date: dueDate.value,
      });
      ElMessage.success("任务建议已确认");
    }
    emit("changed");
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    busy.value = false;
  }
}

async function reject() {
  let reason = "";
  try {
    const result = await ElMessageBox.prompt("可以写一句拒绝原因", "拒绝这条草案", {
      confirmButtonText: "拒绝",
      cancelButtonText: "取消",
      inputPlaceholder: "例如：这条先不做",
    });
    reason = result.value;
  } catch {
    return;
  }
  const id = props.event?.id || props.suggestion?.id;
  if (!id) return;
  busy.value = true;
  try {
    await api.reject(id, { kind: props.event ? "event" : "task", reason: reason || "" });
    ElMessage.success("已拒绝");
    emit("changed");
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    busy.value = false;
  }
}

function openEvidence() {
  if (props.event) {
    store.openEvidence(props.event.source_id, props.event.evidence_excerpt, props.event.evidence_row, props.event.evidence_col);
  } else if (props.suggestion?.source_id) {
    store.openEvidence(props.suggestion.source_id, props.suggestion.evidence_excerpt);
  }
}
</script>

<template>
  <article class="card" :class="{ task: suggestion, risk: event?.conflict_flag }">
    <template v-if="event">
      <div class="card-top">
        <div class="tags">
          <el-tag>{{ labelOf(eventLabels, event.event_type) }}</el-tag>
          <el-tag type="info">{{ event.proposal_origin || "未标明来源" }}</el-tag>
          <el-tag v-if="event.conflict_flag" type="danger">有冲突</el-tag>
        </div>
        <span class="muted">置信度 {{ Math.round(event.confidence * 100) }}%</span>
      </div>
      <p class="change" v-if="additional">建议为该客户新增商品订单</p>
      <div class="change" v-if="additional">
        {{ additional.product }} · {{ additional.quantity }} 件
        <template v-if="additional.delivery_date"> · 交期 {{ additional.delivery_date }}</template>
      </div>
      <div class="change" v-else-if="event.field_name && event.proposed_value">
        {{ labelOf(fieldLabels, event.field_name) }}
        {{ event.current_value || "空" }} → <em>{{ event.proposed_value }}</em>
      </div>
      <p>{{ event.explanation }}</p>
      <p class="muted" v-if="event.conflict_note">{{ event.conflict_note }}</p>
      <el-checkbox v-if="canApply" v-model="applyToOrder">采纳到订单</el-checkbox>
      <p class="muted" v-else-if="writesSupply">确认后更新该商品的供应记录，不改订单数量和交期。</p>
      <p class="muted" v-else-if="event.event_type === 'partial_delivery'">确认后记录已交付数量。订单原数量保留，占用只计未交部分。说一半时，已交付数量为订单数量除以 2。</p>
      <p class="muted" v-else-if="additional">确认后才会为该客户新建订单，订单号由系统生成。拒绝则不建订单。已有供应记录时不会覆盖在手库存。</p>
      <p class="muted" v-else-if="event.event_type === 'commitment'">销售承诺只记录，不会直接改写订单。</p>
    </template>
    <template v-else-if="suggestion">
      <div class="card-top">
        <el-tag type="warning">{{ labelOf(actionLabels, suggestion.action) }}</el-tag>
        <span class="muted" v-if="suggestion.target_task_id">关联任务 #{{ suggestion.target_task_id }}</span>
      </div>
      <el-input v-model="title" style="margin-top: 10px" />
      <div class="samples" style="margin-top: 10px">
        <el-input v-model="owner" placeholder="负责人，可留空表示待指派" />
        <el-date-picker v-model="dueDate" value-format="YYYY-MM-DD" type="date" placeholder="截止日期" />
      </div>
      <p>{{ suggestion.reason }}</p>
      <p class="muted" v-if="suggestion.order_status">确认后，订单状态将变为「{{ suggestion.order_status }}」。</p>
    </template>
    <div class="row-actions">
      <el-button link type="primary" @click="openEvidence">查看证据</el-button>
      <div>
        <el-button :disabled="busy" @click="reject">拒绝</el-button>
        <el-button type="primary" :loading="busy" @click="confirm">确认</el-button>
      </div>
    </div>
  </article>
</template>
