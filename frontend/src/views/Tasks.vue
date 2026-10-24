<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { api } from "../api/client";
import TaskCard from "../components/TaskCard.vue";
import { useWorkspaceStore } from "../stores/workspace";
import type { OrderBrief, TaskItem } from "../types";
import { doneTaskStatuses, openBoardStatuses } from "../utils/labels";

const store = useWorkspaceStore();

const orders = ref<OrderBrief[]>([]);
const tasks = ref<TaskItem[]>([]);
const orderId = ref<number | undefined>(undefined);
const collapsed = ref<Record<string, boolean>>({});
const showDone = ref(false);

function toggleColumn(status: string) {
  collapsed.value[status] = !collapsed.value[status];
}

const columns = computed(() =>
  openBoardStatuses.map((status) => ({
    status,
    items: tasks.value.filter((task) => task.status === status),
  })),
);
const doneTasks = computed(() => tasks.value.filter((task) => doneTaskStatuses.includes(task.status)));

async function reload() {
  tasks.value = await api.tasks(orderId.value ? { order_id: orderId.value } : undefined);
}

watch(
  orderId,
  (id) => {
    store.contextOrderId = id ?? null;
  },
  { immediate: true },
);

watch(
  () => store.revision,
  async () => {
    orders.value = await api.orders();
    await reload();
  },
);

onMounted(async () => {
  orders.value = await api.orders();
  await reload();
});
</script>

<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h1>任务看板</h1>
        <p>待确认的任务建议会先出现在看板上。确认后进入待处理；拒绝的不会留下。完成后收在下方，默认不展开。</p>
      </div>
      <el-select v-model="orderId" clearable placeholder="全部订单" style="width: 220px" @change="reload">
        <el-option v-for="order in orders" :key="order.id" :label="`${order.order_no} ${order.customer_name}`" :value="order.id" />
      </el-select>
    </header>
    <div class="board">
      <div class="columns">
        <section class="column" :class="{ collapsed: collapsed[column.status] }" v-for="column in columns" :key="column.status">
          <button
            type="button"
            class="column-toggle"
            :aria-expanded="!collapsed[column.status]"
            @click="toggleColumn(column.status)"
          >
            <strong>{{ column.status }} <span class="muted">{{ column.items.length }}</span></strong>
            <span class="muted">{{ collapsed[column.status] ? "展开" : "收起" }}</span>
          </button>
          <div class="stack" v-if="!collapsed[column.status]">
            <TaskCard v-for="task in column.items" :key="`${task.kind || 'task'}-${task.id}`" :task="task" @changed="reload" />
            <div v-if="!column.items.length" class="empty">{{ column.status === "待确认" ? "没有待确认事项" : "没有任务" }}</div>
          </div>
        </section>
      </div>
    </div>
    <section class="archive">
      <button type="button" class="archive-toggle" :aria-expanded="showDone" @click="showDone = !showDone">
        <strong>已完成 <span class="muted">{{ doneTasks.length }}</span></strong>
        <span class="muted">{{ showDone ? "收起" : "展开" }}</span>
      </button>
      <div v-if="showDone" class="archive-body">
        <TaskCard v-for="task in doneTasks" :key="`${task.kind || 'task'}-${task.id}`" :task="task" @changed="reload" />
        <div v-if="!doneTasks.length" class="empty">还没有已完成或已关闭的任务。</div>
      </div>
    </section>
  </section>
</template>
