<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, errorMessage } from "../api/client";
import type { TaskItem } from "../types";
import { actionLabels, labelOf, taskStatuses } from "../utils/labels";
import { useWorkspaceStore } from "../stores/workspace";

const props = defineProps<{ task: TaskItem }>();
const emit = defineEmits<{ changed: [] }>();
const store = useWorkspaceStore();
const router = useRouter();
const owner = ref(props.task.owner);
const status = ref(props.task.status);
const busy = ref(false);

async function save(patch: Record<string, unknown>) {
  try {
    await api.patchTask(props.task.id, patch);
    if (patch.status === "已完成" || patch.status === "已关闭") {
      ElMessage.success("已收进「已完成」，展开后可以再查看");
    }
    emit("changed");
  } catch (error) {
    status.value = props.task.status;
    owner.value = props.task.owner;
    ElMessage.error(errorMessage(error));
  }
}

async function confirmSuggestion() {
  if (!props.task.suggestion_id) return;
  busy.value = true;
  try {
    await api.confirm(props.task.suggestion_id, { kind: "task" });
    ElMessage.success("任务建议已确认");
    emit("changed");
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    busy.value = false;
  }
}

async function rejectSuggestion() {
  if (!props.task.suggestion_id) return;
  let reason = "";
  try {
    const result = await ElMessageBox.prompt("可以写一句拒绝原因", "拒绝这条任务建议", {
      confirmButtonText: "拒绝",
      cancelButtonText: "取消",
      inputPlaceholder: "例如：这条先不做",
    });
    reason = result.value;
  } catch {
    return;
  }
  busy.value = true;
  try {
    await api.reject(props.task.suggestion_id, { kind: "task", reason: reason || "" });
    ElMessage.success("已拒绝，不会留在看板上");
    emit("changed");
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <article v-if="task.kind === 'suggestion'" class="card task draft-task">
    <div class="card-top">
      <el-tag type="warning">{{ labelOf(actionLabels, task.action || "create") }}</el-tag>
      <span class="muted">{{ task.order_no }}</span>
    </div>
    <strong>{{ task.title }}</strong>
    <p>{{ task.reason }}</p>
    <div class="muted">负责人 {{ task.owner || "待指派" }} · 截止 {{ task.due_date || "未定" }}</div>
    <div class="row-actions" style="margin-top: 8px">
      <el-button link type="primary" :disabled="!task.source_id" @click="store.openEvidence(task.source_id, task.evidence_excerpt)">
        查看证据
      </el-button>
      <el-button link @click="router.push(`/orders/${task.order_id}/review`)">去审核</el-button>
    </div>
    <div class="row-actions">
      <el-button :disabled="busy" @click="rejectSuggestion">拒绝</el-button>
      <el-button type="primary" :loading="busy" @click="confirmSuggestion">确认</el-button>
    </div>
  </article>
  <article v-else class="card task">
    <div class="muted">{{ task.order_no }}</div>
    <strong>{{ task.title }}</strong>
    <p>{{ task.reason }}</p>
    <el-input v-model="owner" placeholder="负责人" @change="save({ owner })" />
    <div class="muted" style="margin: 8px 0">截止 {{ task.due_date || "未定" }}</div>
    <el-select v-model="status" @change="save({ status })">
      <el-option v-for="item in taskStatuses" :key="item" :label="item" :value="item" />
    </el-select>
    <div class="row-actions" style="margin-top: 8px">
      <el-button link type="primary" :disabled="!task.source_id" @click="store.openEvidence(task.source_id, task.evidence_excerpt)">
        查看证据
      </el-button>
    </div>
  </article>
</template>
