<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, errorMessage } from "../api/client";
import CreateRecordDialog from "../components/CreateRecordDialog.vue";
import OrderTimeline from "../components/OrderTimeline.vue";
import { useWorkspaceStore } from "../stores/workspace";
import type { Customer, OrderBrief, OrderDetail } from "../types";

const store = useWorkspaceStore();

const router = useRouter();
const allOrders = ref<OrderBrief[]>([]);
const customers = ref<Customer[]>([]);
const selectedId = ref<number | null>(null);
const selectedCustomerId = ref<number | null>(null);
const detail = ref<OrderDetail | null>(null);
const creatingCustomer = ref(false);
const creatingOrder = ref(false);
const showTrash = ref(false);
const customerFilter = ref<string | null>(null);
const productFilter = ref<string | null>(null);
const showImports = ref(false);

const customerNames = computed(() =>
  unique([...customers.value.map((item) => item.name), ...allOrders.value.map((item) => item.customer_name)]),
);
const productNames = computed(() => {
  const source = customerFilter.value
    ? allOrders.value.filter((item) => item.customer_name === customerFilter.value)
    : allOrders.value;
  return unique(source.map((item) => item.product).filter((name) => name));
});
const filteredOrders = computed(() =>
  allOrders.value.filter((item) => {
    if (customerFilter.value && item.customer_name !== customerFilter.value) return false;
    if (productFilter.value && item.product !== productFilter.value) return false;
    return true;
  }),
);
const selected = computed(() => filteredOrders.value.find((item) => item.id === selectedId.value) || null);
const bareCustomers = computed(() => {
  if (showTrash.value || productFilter.value) return [];
  return customers.value.filter(
    (item) => (item.order_count || 0) === 0 && (!customerFilter.value || item.name === customerFilter.value),
  );
});
const selectedCustomer = computed(
  () => customers.value.find((item) => item.id === selectedCustomerId.value) || null,
);
const currentCustomer = computed(() => {
  if (detail.value?.customer) return { id: detail.value.customer.id, name: detail.value.customer.name };
  if (selectedCustomer.value) return { id: selectedCustomer.value.id, name: selectedCustomer.value.name };
  return null;
});

function unique(values: string[]) {
  return [...new Set(values)];
}

async function loadOrders() {
  const [orders, people] = await Promise.all([
    api.orders(showTrash.value ? { trashed: true } : undefined),
    api.customers(),
  ]);
  allOrders.value = orders;
  customers.value = people;
}

async function selectOrder(id: number) {
  selectedCustomerId.value = null;
  selectedId.value = id;
  detail.value = await api.order(id);
}

function selectCustomer(id: number) {
  selectedId.value = null;
  detail.value = null;
  selectedCustomerId.value = id;
  store.contextOrderId = null;
}

function openWarehouse() {
  const product = detail.value?.supply?.product || detail.value?.order.product;
  if (!product) return;
  router.push({ path: "/warehouse", query: { product } });
}

function openReview() {
  if (!selectedId.value) return;
  router.push(`/orders/${selectedId.value}/review`);
}

async function onCreated(payload: { orderId: number | null; customerId: number }) {
  customerFilter.value = null;
  productFilter.value = null;
  showTrash.value = false;
  await loadOrders();
  if (payload.orderId) await selectOrder(payload.orderId);
  else selectCustomer(payload.customerId);
}

async function toggleTrash() {
  showTrash.value = !showTrash.value;
  await loadOrders();
}

async function trashOrder(order: OrderBrief) {
  try {
    await ElMessageBox.confirm(
      `「${order.order_no}」会移入回收站。沟通、任务和证据会保留，之后可以恢复。`,
      "删除订单",
      { confirmButtonText: "移入回收站", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  try {
    await api.deleteOrder(order.id);
    ElMessage.success("已移入回收站");
    if (selectedId.value === order.id) selectedId.value = null;
    await loadOrders();
  } catch (error) {
    ElMessage.error(errorMessage(error));
  }
}

async function restoreOrder(order: OrderBrief) {
  try {
    await api.restoreOrder(order.id);
    ElMessage.success("已恢复到工作台");
    if (selectedId.value === order.id) selectedId.value = null;
    await loadOrders();
  } catch (error) {
    ElMessage.error(errorMessage(error));
  }
}

function onMore(command: string | number | object, order: OrderBrief | null) {
  if (!order) return;
  if (command === "delete") void trashOrder(order);
  if (command === "purge") void purgeOrder(order);
}

async function purgeOrder(order: OrderBrief) {
  try {
    await ElMessageBox.confirm(
      `彻底删除「${order.order_no}」后无法恢复。这张订单的沟通和未完成任务会删除。已完成和已关闭的任务会留在看板上，仓库商品和其他订单不会删除。`,
      "彻底删除",
      { confirmButtonText: "彻底删除", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  try {
    await api.purgeOrder(order.id);
    ElMessage.success("已彻底删除");
    if (selectedId.value === order.id) selectedId.value = null;
    await loadOrders();
  } catch (error) {
    ElMessage.error(errorMessage(error));
  }
}

function stampImport(value: string) {
  return (value || "").replace("T", " ");
}

watch(selectedId, (id) => {
  showImports.value = false;
  store.contextOrderId = id;
});

watch(
  () => store.revision,
  async () => {
    await loadOrders();
    if (selectedId.value != null) await selectOrder(selectedId.value);
  },
);

watch(productNames, (names) => {
  if (productFilter.value && !names.includes(productFilter.value)) productFilter.value = null;
});

watch(filteredOrders, (list) => {
  if (selectedCustomerId.value && selectedId.value == null) return;
  if (!list.length) {
    selectedId.value = null;
    detail.value = null;
    return;
  }
  if (!list.some((item) => item.id === selectedId.value)) {
    void selectOrder(list[0].id);
  }
});

onMounted(loadOrders);
</script>

<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h1>{{ showTrash ? "回收站" : "订单工作台" }}</h1>
        <p v-if="showTrash">这里的订单不会出现在工作台和任务看板上。恢复后，待确认事项会重新显示。</p>
        <p v-else>同一商品共用在手数量和最早可完工日期。已确认的未交数量才占用库存。在手和最早可完工日期请到仓库修改。</p>
      </div>
      <div class="row-actions">
        <el-button @click="toggleTrash">{{ showTrash ? "返回工作台" : "回收站" }}</el-button>
        <el-button v-if="!showTrash" type="primary" @click="creatingCustomer = true">新建客户</el-button>
      </div>
    </header>
    <div class="layout">
      <div class="panel order-list">
        <div class="filters">
          <el-select v-model="customerFilter" clearable filterable placeholder="按客户筛选">
            <el-option v-for="name in customerNames" :key="name" :label="name" :value="name" />
          </el-select>
          <el-select v-model="productFilter" clearable filterable placeholder="按商品筛选">
            <el-option v-for="name in productNames" :key="name" :label="name" :value="name" />
          </el-select>
        </div>
        <div v-if="!allOrders.length && !bareCustomers.length" class="empty">
          {{ showTrash ? "回收站是空的。" : "还没有客户。可以先新建客户，暂不建订单。" }}
        </div>
        <div v-else-if="!filteredOrders.length && !bareCustomers.length" class="empty">没有符合当前筛选的订单。</div>
        <div
          v-for="customer in bareCustomers"
          :key="`customer-${customer.id}`"
          class="order-row"
          :class="{ active: customer.id === selectedCustomerId && selectedId == null }"
        >
          <button class="order-button" @click="selectCustomer(customer.id)">
            <strong>{{ customer.name }}</strong>
            <span class="muted">还没有订单</span>
          </button>
        </div>
        <div
          v-for="order in filteredOrders"
          :key="order.id"
          class="order-row"
          :class="{ active: order.id === selectedId }"
        >
          <button class="order-button" @click="selectOrder(order.id)">
            <strong>{{ order.order_no }} · {{ order.customer_name }}</strong>
            <span class="muted">{{ order.product }} · {{ order.status }}</span>
            <span class="muted" v-if="order.pending_count">{{ order.pending_count }} 条待确认</span>
          </button>
          <div class="order-side">
            <button v-if="showTrash" type="button" class="text-button" @click="restoreOrder(order)">恢复</button>
            <el-dropdown trigger="click" @command="(command: string | number | object) => onMore(command, order)">
              <button type="button" class="text-button">更多</button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-if="!showTrash" command="delete" class="menu-danger">删除</el-dropdown-item>
                  <el-dropdown-item v-else command="purge" class="menu-danger">彻底删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
      </div>
      <div class="panel" v-if="selectedCustomer && !selected">
        <div class="section">
          <div class="card-top">
            <h2>{{ selectedCustomer.name }}</h2>
            <el-button type="primary" @click="creatingOrder = true">新建订单</el-button>
          </div>
          <p class="muted" v-if="selectedCustomer.contact">联系人 {{ selectedCustomer.contact }}</p>
          <p class="muted" v-if="selectedCustomer.note">{{ selectedCustomer.note }}</p>
          <p>这个客户还没有订单。点「新建订单」添加第一张，之后还可以继续加。</p>
        </div>
      </div>
      <div class="panel" v-else-if="detail && selected">
        <div class="facts">
          <div class="fact">
            <span>订单数量</span>
            <strong>{{ detail.order.quantity }} 件</strong>
            <span class="muted" v-if="detail.order.delivered_qty">已交付 {{ detail.order.delivered_qty }}，未交 {{ detail.order.quantity - detail.order.delivered_qty }}</span>
          </div>
          <div class="fact">
            <span>订单交期</span>
            <strong>{{ detail.order.delivery_date }}</strong>
          </div>
          <div class="fact">
            <span>订单状态</span>
            <strong><span class="status" :data-status="detail.order.status">{{ detail.order.status }}</span></strong>
          </div>
          <div class="fact" v-if="detail.supply">
            <span>已占用</span>
            <strong>{{ detail.supply.reserved }} 件</strong>
          </div>
          <button type="button" class="fact link" v-if="detail.supply" title="到仓库修改" @click="openWarehouse">
            <span>在手</span>
            <strong>{{ detail.supply.on_hand }} 件</strong>
          </button>
          <button
            type="button"
            class="fact link"
            v-if="detail.supply"
            :class="{ danger: detail.supply.committable < 0 }"
            title="到仓库修改"
            @click="openWarehouse"
          >
            <span>还可承诺</span>
            <strong>{{ detail.supply.committable }} 件</strong>
          </button>
          <button type="button" class="fact link" v-if="detail.supply" title="到仓库修改" @click="openWarehouse">
            <span>最早可完工日期</span>
            <strong>{{ detail.supply.earliest_completion }}</strong>
          </button>
        </div>
        <div class="section">
          <div class="card-top">
            <h2>时间线</h2>
            <div class="row-actions">
              <template v-if="showTrash">
                <el-button @click="restoreOrder(selected)">恢复</el-button>
              </template>
              <template v-else>
                <el-button @click="creatingOrder = true">为该客户新建订单</el-button>
              </template>
              <el-dropdown trigger="click" @command="(command: string | number | object) => onMore(command, selected)">
                <el-button>更多</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item v-if="!showTrash" command="delete" class="menu-danger">删除</el-dropdown-item>
                    <el-dropdown-item v-else command="purge" class="menu-danger">彻底删除</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              <el-button v-if="!showTrash" type="primary" @click="openReview">详情</el-button>
            </div>
          </div>
          <p class="muted" v-if="detail.supply">{{ detail.supply.note }}</p>
          <OrderTimeline :events="detail.timeline" />
        </div>
        <div class="section import-history">
          <button type="button" class="archive-toggle" :aria-expanded="showImports" @click="showImports = !showImports">
            <strong>历史导入 <span class="muted">{{ detail.imports.length }}</span></strong>
            <span class="muted">{{ showImports ? "收起" : "展开" }}</span>
          </button>
          <div v-if="showImports">
            <div v-if="!detail.imports.length" class="empty">还没有导入记录。</div>
            <article v-for="item in detail.imports" :key="`${item.scope}-${item.id}`" class="import-item">
              <div class="muted">{{ stampImport(item.created_at) }}<template v-if="item.title"> · {{ item.title }}</template></div>
              <p class="summary-text">{{ item.summary }}</p>
            </article>
          </div>
        </div>
      </div>
    </div>
    <CreateRecordDialog v-model="creatingCustomer" :customer="null" @created="onCreated" />
    <CreateRecordDialog v-model="creatingOrder" :customer="currentCustomer" @created="onCreated" />
  </section>
</template>
