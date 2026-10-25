<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, errorMessage } from "../api/client";

const props = defineProps<{
  modelValue: boolean;
  customer: { id: number; name: string } | null;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  created: [payload: { orderId: number | null; customerId: number }];
}>();

const saving = ref(false);
const extracting = ref(false);
const fromChat = ref(false);
const withoutOrder = ref(false);
const chatText = ref("");
const pickedFiles = ref<File[]>([]);
const sheetLines = ref<SheetLine[]>([]);
const missing = ref<string[]>([]);

type SheetLine = {
  orderNo: string;
  product: string;
  quantity: number | null;
  deliveryDate: string;
  availableQty: number | null;
  earliest: string;
};
const otherCustomerNote = ref("");
const form = reactive({
  name: "",
  contact: "",
  note: "",
  orderNo: "",
  product: "",
  quantity: 100 as number | null,
  deliveryDate: "",
  owner: "订单负责人",
  availableQty: 100 as number | null,
  earliest: "",
  supplyNote: "",
});

const creatingCustomer = computed(() => props.customer === null);
const title = computed(() => (creatingCustomer.value ? "新建客户" : `为${props.customer?.name}新建订单`));
const shownMissing = computed(() => {
  if (!creatingCustomer.value) return missing.value.filter((item) => item !== "客户名称");
  if (withoutOrder.value) return missing.value.filter((item) => item === "客户名称");
  return missing.value;
});

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    fromChat.value = false;
    withoutOrder.value = false;
    chatText.value = "";
    pickedFiles.value = [];
    sheetLines.value = [];
    missing.value = [];
    otherCustomerNote.value = "";
    form.name = "";
    form.contact = "";
    form.note = "";
    form.orderNo = "";
    form.product = "";
    form.quantity = 100;
    form.deliveryDate = "";
    form.owner = "订单负责人";
    form.availableQty = 100;
    form.earliest = "";
    form.supplyNote = "";
  },
);

function useChat() {
  fromChat.value = true;
  form.quantity = null;
  form.availableQty = null;
  missing.value = [];
}

function useManual() {
  fromChat.value = false;
  pickedFiles.value = [];
  sheetLines.value = [];
  missing.value = [];
  if (form.quantity == null) form.quantity = 100;
  if (form.availableQty == null) form.availableQty = 100;
}

function clearMissing(label: string) {
  missing.value = missing.value.filter((item) => item !== label);
}

function onChatFile(upload: { raw?: File }) {
  const file = upload.raw;
  if (!file) return;
  const known = pickedFiles.value.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified);
  if (!known) pickedFiles.value.push(file);
}

function removeFile(index: number) {
  pickedFiles.value.splice(index, 1);
}

async function extractChat() {
  const pasted = chatText.value.trim();
  if (!pasted && !pickedFiles.value.length) {
    ElMessage.warning("先粘贴文本，或上传文件");
    return;
  }
  extracting.value = true;
  try {
    const profile = pickedFiles.value.length
      ? await api.previewCustomerFile(fileForm(pickedFiles.value, pasted))
      : await api.previewCustomer(pasted);
    if (creatingCustomer.value) {
      form.name = profile.name || "";
      form.contact = profile.contact || "";
      form.note = profile.note || "";
      otherCustomerNote.value = "";
    } else {
      const extractedName = String(profile.name || "").trim();
      const currentName = (props.customer?.name || "").trim();
      otherCustomerNote.value =
        extractedName && extractedName !== currentName
          ? `文本里的客户是「${extractedName}」，仍为当前客户建立订单。`
          : "";
    }
    const orders = Array.isArray(profile.orders) ? profile.orders : [];
    sheetLines.value =
      orders.length > 1
        ? orders.map((line: Record<string, unknown>) => ({
            orderNo: String(line.order_no || ""),
            product: String(line.product || ""),
            quantity: typeof line.quantity === "number" ? line.quantity : null,
            deliveryDate: String(line.delivery_date || ""),
            availableQty: typeof line.on_hand === "number" ? line.on_hand : null,
            earliest: String(line.earliest_completion || ""),
          }))
        : [];
    form.orderNo = profile.order_no || "";
    form.product = profile.product || "";
    form.quantity = profile.quantity ?? null;
    form.deliveryDate = profile.delivery_date || "";
    form.availableQty = profile.on_hand ?? null;
    form.earliest = profile.earliest_completion || "";
    form.supplyNote = "";
    missing.value = sheetLines.value.length
      ? creatingCustomer.value && !String(profile.name || "").trim()
        ? ["客户名称"]
        : []
      : Array.isArray(profile.missing)
        ? profile.missing
        : [];
    if (shownMissing.value.length) {
      ElMessage.warning(`请补全：${shownMissing.value.join("、")}`);
    } else {
      ElMessage.success("已从文本或文件填入，请核对后再建立");
    }
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    extracting.value = false;
  }
}

function fileForm(files: File[], pasted: string) {
  const formData = new FormData();
  for (const file of files) formData.append("files", file, file.name);
  if (pasted) formData.append("text", pasted);
  return formData;
}

function linePayload(line: SheetLine) {
  return {
    order_no: line.orderNo.trim(),
    product: line.product.trim(),
    quantity: line.quantity,
    delivery_date: line.deliveryDate,
    owner: form.owner.trim() || "订单负责人",
    available_qty: line.availableQty,
    earliest_completion: line.earliest,
    supply_note: form.supplyNote.trim(),
  };
}

function close() {
  emit("update:modelValue", false);
}

function orderPayload() {
  return {
    order_no: form.orderNo.trim(),
    product: form.product.trim(),
    quantity: form.quantity,
    delivery_date: form.deliveryDate,
    owner: form.owner.trim() || "订单负责人",
    available_qty: form.availableQty,
    earliest_completion: form.earliest,
    supply_note: form.supplyNote.trim(),
  };
}

async function submit() {
  if (creatingCustomer.value && withoutOrder.value) {
    if (!form.name.trim()) {
      missing.value = ["客户名称"];
      ElMessage.warning("请补全：客户名称");
      return;
    }
    saving.value = true;
    try {
      const created = await api.createCustomer({
        name: form.name.trim(),
        contact: form.contact.trim(),
        note: form.note.trim(),
      });
      ElMessage.success("客户已建立，可以稍后添加订单");
      emit("created", { orderId: null, customerId: created.customer.id });
      close();
    } catch (error) {
      ElMessage.error(errorMessage(error));
    } finally {
      saving.value = false;
    }
    return;
  }
  if (sheetLines.value.length > 1) {
    const rowGaps = sheetLines.value.flatMap((line, index) => {
      const gaps = [
        !line.orderNo.trim() ? "订单号" : "",
        line.quantity == null ? "数量" : "",
        !line.deliveryDate ? "订单交期" : "",
        line.availableQty == null ? "在手数量" : "",
        !line.earliest ? "最早可完工日期" : "",
      ].filter(Boolean);
      return gaps.length ? `第 ${index + 1} 行${gaps.join("、")}` : "";
    }).filter(Boolean);
    const nameGap = creatingCustomer.value && !form.name.trim() ? "客户名称" : "";
    const gaps = [nameGap, ...rowGaps].filter(Boolean);
    if (gaps.length) {
      missing.value = nameGap ? ["客户名称"] : [];
      ElMessage.warning(`请补全：${gaps.join("；")}`);
      return;
    }
    saving.value = true;
    try {
      const orders = sheetLines.value.map(linePayload);
      const created = creatingCustomer.value
        ? await api.createCustomer({
            name: form.name.trim(),
            contact: form.contact.trim(),
            note: form.note.trim(),
            orders,
          })
        : await api.createOrders({ customer_id: props.customer?.id, orders });
      const orderId = creatingCustomer.value ? created.order.id : created.orders[0].id;
      const customerId = creatingCustomer.value ? created.customer.id : props.customer?.id;
      ElMessage.success(`已建立 ${orders.length} 张订单`);
      emit("created", { orderId, customerId });
      close();
    } catch (error) {
      ElMessage.error(errorMessage(error));
    } finally {
      saving.value = false;
    }
    return;
  }
  const gaps = [
    creatingCustomer.value && !form.name.trim() ? "客户名称" : "",
    !form.orderNo.trim() ? "订单号" : "",
    form.quantity == null ? "数量" : "",
    !form.deliveryDate ? "订单交期" : "",
    form.availableQty == null ? "在手数量" : "",
    !form.earliest ? "最早可完工日期" : "",
  ].filter(Boolean);
  if (gaps.length) {
    missing.value = gaps;
    ElMessage.warning(`请补全：${gaps.join("、")}`);
    return;
  }
  saving.value = true;
  try {
    const created = creatingCustomer.value
      ? await api.createCustomer({
          name: form.name.trim(),
          contact: form.contact.trim(),
          note: form.note.trim(),
          order: orderPayload(),
        })
      : await api.createOrder({ customer_id: props.customer?.id, ...orderPayload() });
    const orderId = creatingCustomer.value ? created.order.id : created.id;
    const customerId = creatingCustomer.value ? created.customer.id : props.customer?.id || created.customer_id;
    ElMessage.success(creatingCustomer.value ? "客户和订单已建立" : "订单已建立");
    emit("created", { orderId, customerId });
    close();
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    :width="sheetLines.length > 1 ? '920px' : '640px'"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="muted dialog-lead">
      {{
        creatingCustomer
          ? "可以先只建客户。之后点这个客户再加订单，同一个客户可以有多张订单。"
          : "新订单沿用当前客户。订货表和聊天记录可以一起上传。同一商品已有供应记录时，这里的在手数量和最早可完工日期不会覆盖它。"
      }}
    </p>
    <div class="samples" style="margin-bottom: 12px">
      <el-button :type="fromChat ? 'default' : 'primary'" @click="useManual">手工填写</el-button>
      <el-button :type="fromChat ? 'primary' : 'default'" @click="useChat">根据文本或文件</el-button>
      <el-checkbox v-if="creatingCustomer" v-model="withoutOrder">暂不建订单</el-checkbox>
    </div>
    <div v-if="fromChat" class="form-span" style="margin-bottom: 12px">
      <el-input v-model="chatText" type="textarea" :rows="6" placeholder="粘贴文本。订货表、聊天记录、PDF、图片可以一次选多个。" />
      <p v-for="(file, index) in pickedFiles" :key="`${file.name}-${index}`" class="muted" style="margin: 8px 0 0">
        已选文件：{{ file.name }}
        <button type="button" class="text-button" @click="removeFile(index)">移除</button>
      </p>
      <div class="row-actions" style="margin-top: 8px">
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          multiple
          accept=".txt,.md,.csv,.json,.html,.pdf,.png,.jpg,.jpeg,.webp,.docx,.xlsx,.xlsm,.mp3,.wav,.m4a"
          :on-change="onChatFile"
        >
          <el-button>上传文件</el-button>
        </el-upload>
        <el-button type="primary" :loading="extracting" @click="extractChat">提炼信息</el-button>
      </div>
    </div>
    <p v-if="otherCustomerNote" class="muted" style="margin: 0 0 12px">{{ otherCustomerNote }}</p>
    <el-alert
      v-if="shownMissing.length"
      type="warning"
      :closable="false"
      :title="`文本或文件里没有这些必填项，请人工填写：${shownMissing.join('、')}`"
      style="margin-bottom: 12px"
    />
    <el-form label-position="top" class="form-grid" @submit.prevent="submit">
      <template v-if="creatingCustomer">
        <el-form-item label="客户名称" required :error="shownMissing.includes('客户名称') ? '请填写' : ''">
          <el-input v-model="form.name" maxlength="120" placeholder="例如 青岚电子" @input="clearMissing('客户名称')" />
        </el-form-item>
        <el-form-item label="联系人">
          <el-input v-model="form.contact" maxlength="120" placeholder="例如 采购 小周" />
        </el-form-item>
        <el-form-item label="备注" class="form-span">
          <el-input v-model="form.note" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </template>
      <template v-if="sheetLines.length > 1 && (!creatingCustomer || !withoutOrder)">
        <el-form-item label="负责人">
          <el-input v-model="form.owner" maxlength="80" />
        </el-form-item>
        <el-form-item label="供应说明" class="form-span">
          <el-input v-model="form.supplyNote" type="textarea" :rows="2" placeholder="留空时按库存和最早可完工日期生成" />
        </el-form-item>
        <div class="form-span">
          <p class="muted" style="margin: 0 0 8px">表里有 {{ sheetLines.length }} 行，会建成 {{ sheetLines.length }} 张订单。订单号、在手数量和最早可完工日期表里没有的，请逐行补上。</p>
          <div v-for="(line, index) in sheetLines" :key="index" style="padding: 8px 0; border-top: 1px solid #e4ded4">
            <strong>{{ index + 1 }}. {{ line.product || "未命名商品" }}</strong>
            <div class="line-fields" style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px">
              <el-input v-model="line.orderNo" maxlength="40" placeholder="订单号" style="width: 140px" />
              <el-input-number v-model="line.quantity" :min="1" :step="1" :value-on-clear="null" controls-position="right" />
              <el-date-picker v-model="line.deliveryDate" type="date" value-format="YYYY-MM-DD" placeholder="订单交期" />
              <el-input-number v-model="line.availableQty" :min="0" :step="1" :value-on-clear="null" controls-position="right" />
              <el-date-picker v-model="line.earliest" type="date" value-format="YYYY-MM-DD" placeholder="最早可完工" />
            </div>
          </div>
        </div>
      </template>
      <template v-else-if="!creatingCustomer || !withoutOrder">
      <el-form-item label="订单号" required :error="shownMissing.includes('订单号') ? '请填写' : ''">
        <el-input v-model="form.orderNo" maxlength="40" placeholder="例如 ORD-301" @input="clearMissing('订单号')" />
      </el-form-item>
      <el-form-item label="产品">
        <el-input v-model="form.product" maxlength="120" placeholder="例如 贴片电阻" />
      </el-form-item>
      <el-form-item label="数量" required :error="shownMissing.includes('数量') ? '请填写' : ''">
        <el-input-number v-model="form.quantity" :min="1" :step="1" :value-on-clear="null" controls-position="right" @change="clearMissing('数量')" />
      </el-form-item>
      <el-form-item label="订单交期" required :error="shownMissing.includes('订单交期') ? '请填写' : ''">
        <el-date-picker v-model="form.deliveryDate" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" @change="clearMissing('订单交期')" />
      </el-form-item>
      <el-form-item label="负责人">
        <el-input v-model="form.owner" maxlength="80" />
      </el-form-item>
      <el-form-item label="在手数量" required :error="shownMissing.includes('在手数量') ? '请填写' : ''">
        <el-input-number v-model="form.availableQty" :min="0" :step="1" :value-on-clear="null" controls-position="right" @change="clearMissing('在手数量')" />
      </el-form-item>
      <el-form-item label="最早可完工日期" required :error="shownMissing.includes('最早可完工日期') ? '请填写' : ''">
        <el-date-picker v-model="form.earliest" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" @change="clearMissing('最早可完工日期')" />
      </el-form-item>
      <el-form-item label="供应说明" class="form-span">
        <el-input v-model="form.supplyNote" type="textarea" :rows="2" placeholder="留空时按库存和最早可完工日期生成" />
      </el-form-item>
      </template>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">{{ creatingCustomer && withoutOrder ? "只建立客户" : sheetLines.length > 1 ? `建立 ${sheetLines.length} 张订单` : creatingCustomer ? "建立客户" : "建立订单" }}</el-button>
    </template>
  </el-dialog>
</template>
