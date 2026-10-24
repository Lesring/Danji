<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, errorMessage } from "../api/client";
import { useWorkspaceStore } from "../stores/workspace";
import type { AssistantAction } from "../types";
import { labelOf, sourceLabels } from "../utils/labels";

interface Card extends AssistantAction {
  done: boolean;
  busy: boolean;
  skipped: boolean;
  applyToOrder: boolean;
}

interface Turn {
  role: "user" | "assistant";
  content: string;
  notes: string[];
  cards: Card[];
}

const open = defineModel<boolean>({ default: false });
const route = useRoute();
const router = useRouter();
const store = useWorkspaceStore();
const draft = ref("");
const busy = ref(false);
const messages = ref<Turn[]>([]);
const log = ref<HTMLElement | null>(null);

const routeName = computed(() => {
  const name = route.name;
  if (name === "review" || name === "tasks" || name === "warehouse" || name === "workbench") return name;
  return "workbench";
});

const orderId = computed(() => {
  if (route.name === "review") {
    const value = Number(route.params.id);
    return Number.isInteger(value) && value > 0 ? value : null;
  }
  if (route.name === "workbench" || route.name === "tasks") return store.contextOrderId;
  return null;
});

const product = computed(() =>
  route.name === "warehouse" && typeof route.query.product === "string" ? route.query.product : null,
);

function toCard(action: AssistantAction): Card {
  return { ...action, done: false, busy: false, skipped: false, applyToOrder: false };
}

function isImmediate(action: AssistantAction) {
  return action.type === "navigate" || action.type === "open_evidence";
}

function runImmediate(actions: AssistantAction[]) {
  for (const action of actions) {
    if (action.type === "open_evidence" && action.source_id) {
      store.openEvidence(action.source_id, action.excerpt || "", action.row ?? null, action.col ?? null);
    }
    if (action.type !== "navigate") continue;
    if (action.route === "review" && action.order_id) router.push(`/orders/${action.order_id}/review`);
    else if (action.route === "tasks") router.push("/tasks");
    else if (action.route === "warehouse") {
      router.push(action.product ? { path: "/warehouse", query: { product: action.product } } : "/warehouse");
    } else if (action.route === "workbench") router.push("/");
  }
}

async function scrollDown() {
  await nextTick();
  if (log.value) log.value.scrollTop = log.value.scrollHeight;
}

async function send() {
  const content = draft.value.trim();
  if (!content || busy.value) return;
  draft.value = "";
  messages.value.push({ role: "user", content, notes: [], cards: [] });
  busy.value = true;
  await scrollDown();
  try {
    const history = messages.value.slice(-8).map((item) => ({ role: item.role, content: item.content }));
    const body = (await api.assistantTurn({
      route: routeName.value,
      order_id: orderId.value,
      product: product.value,
      messages: history,
    })) as { reply: string; actions: AssistantAction[] };
    const actions = Array.isArray(body.actions) ? body.actions : [];
    messages.value.push({
      role: "assistant",
      content: body.reply || "",
      notes: actions.filter(isImmediate).map((item) => item.summary || "").filter(Boolean),
      cards: actions.filter((item) => !isImmediate(item)).map(toCard),
    });
    runImmediate(actions);
  } catch (error) {
    messages.value.push({ role: "assistant", content: errorMessage(error), notes: [], cards: [] });
  } finally {
    busy.value = false;
    await scrollDown();
  }
}

async function runCard(card: Card) {
  if (card.done || card.busy) return;
  card.busy = true;
  try {
    if (card.type === "create_source") {
      await api.createSource(card.order_id || 0, {
        type: card.source_type || "chat",
        title: card.title || "",
        text: card.text || "",
      });
      ElMessage.success("已生成草案，订单字段尚未改写");
    } else if (card.type === "confirm_draft") {
      const body: Record<string, unknown> = {
        kind: card.kind,
        apply_to_order: Boolean(card.can_apply && card.applyToOrder),
      };
      if (card.kind === "task") {
        if (card.title) body.title = card.title;
        if (card.owner) body.owner = card.owner;
        if (card.due_date) body.due_date = card.due_date;
      }
      await api.confirm(card.draft_id || 0, body);
      ElMessage.success(card.can_apply && card.applyToOrder ? "已写入订单" : "已确认，未改写订单");
    } else if (card.type === "reject_draft") {
      await api.reject(card.draft_id || 0, { kind: card.kind, reason: card.reason || "" });
      ElMessage.success("已拒绝");
    } else if (card.type === "patch_task") {
      await api.patchTask(card.task_id || 0, card.patch || {});
      ElMessage.success("任务已更新");
    } else if (card.type === "update_supplies") {
      for (const item of card.items || []) {
        await api.patchWarehouse({
          product: item.product,
          on_hand: item.on_hand,
          earliest_completion: item.earliest_completion,
          note: item.note,
          name: item.name,
        });
      }
      ElMessage.success("仓库商品已更新，订单未改");
      if (route.name !== "warehouse") router.push("/warehouse");
    } else if (card.type === "delete_supply") {
      await api.deleteWarehouse(card.product || "");
      ElMessage.success("已从仓库删除");
      if (route.name !== "warehouse") router.push("/warehouse");
    }
    card.done = true;
    store.bumpRevision();
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    card.busy = false;
  }
}

function skip(card: Card) {
  card.skipped = true;
  card.done = true;
}

function actionLabel(card: Card) {
  if (card.type === "create_source") return "录入";
  if (card.type === "reject_draft") return "拒绝";
  if (card.type === "patch_task" || card.type === "update_supplies") return "更新";
  if (card.type === "delete_supply") return "删除";
  return "确认";
}
</script>

<template>
  <el-drawer v-model="open" title="对话" size="420px" class="assistant-drawer">
    <div class="assistant">
      <div class="assistant-log" ref="log">
        <p v-if="!messages.length" class="muted">
          可以在任意页面操作订单、任务和仓库。改库存、删商品、写入数量和交期前，仍需要在卡片上点一次。
        </p>
        <article v-for="(message, index) in messages" :key="index" :class="message.role">
          <p class="assistant-text">{{ message.content }}</p>
          <p v-for="(note, noteIndex) in message.notes" :key="noteIndex" class="muted">{{ note }}</p>
          <div v-for="(card, cardIndex) in message.cards" :key="cardIndex" class="card assistant-card">
            <p>{{ card.summary }}</p>
            <p v-if="card.type === 'create_source'" class="muted">
              {{ labelOf(sourceLabels, card.source_type || "chat") }}
              <span v-if="card.title"> · {{ card.title }}</span>
            </p>
            <blockquote v-if="card.text" class="quote">{{ card.text }}</blockquote>
            <el-checkbox v-if="card.can_apply && !card.done" v-model="card.applyToOrder">采纳到订单</el-checkbox>
            <div class="row-actions">
              <span v-if="card.done" class="muted">{{ card.skipped ? "已忽略" : "已执行" }}</span>
              <span v-else></span>
              <span v-if="!card.done" class="samples">
                <button type="button" class="text-button" @click="skip(card)">先不处理</button>
                <el-button size="small" type="primary" :loading="card.busy" @click="runCard(card)">
                  {{ actionLabel(card) }}
                </el-button>
              </span>
            </div>
          </div>
        </article>
      </div>
      <form class="assistant-form" @submit.prevent="send">
        <el-input
          v-model="draft"
          type="textarea"
          :rows="2"
          placeholder="例如：打开工作台"
          @keydown.enter.exact.prevent="send"
        />
        <el-button type="primary" native-type="submit" :loading="busy" :disabled="!draft.trim()">发送</el-button>
      </form>
    </div>
  </el-drawer>
</template>
