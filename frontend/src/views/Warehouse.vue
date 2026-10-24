<script setup lang="ts">
import { nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, errorMessage } from "../api/client";
import { useWorkspaceStore } from "../stores/workspace";
import type { WarehouseItem } from "../types";

const store = useWorkspaceStore();

interface Editable extends WarehouseItem {
  draftOnHand: number;
  draftEarliest: string;
  saving: boolean;
}

const route = useRoute();
const rows = ref<Editable[]>([]);
const focus = ref("");
const creating = ref(false);
const editing = ref(false);
const savingProduct = ref(false);
const savingEdit = ref(false);
const form = reactive({
  product: "",
  onHand: 0,
  earliest: "",
  note: "",
});
const editForm = reactive({
  product: "",
  name: "",
  onHand: 0,
  earliest: "",
  note: "",
});

function anchorId(product: string) {
  return `supply-${encodeURIComponent(product)}`;
}

function stamp(row: WarehouseItem) {
  const time = (row.updated_at || "").replace("T", " ");
  return `${row.updated_by || "系统"}${time ? " · " + time : ""}`;
}

async function load(highlight = "") {
  try {
    const list = await api.warehouse();
    if (!Array.isArray(list)) {
      throw new Error("仓库接口没有返回商品列表");
    }
    rows.value = list.map((item) => ({
      ...item,
      draftOnHand: item.on_hand,
      draftEarliest: item.earliest_completion,
      saving: false,
    }));
  } catch (error) {
    rows.value = [];
    ElMessage.error(errorMessage(error));
    return;
  }
  await nextTick();
  if (highlight) {
    focus.value = highlight;
    document.getElementById(anchorId(highlight))?.scrollIntoView({ block: "center" });
    return;
  }
  reveal();
}

function reveal() {
  focus.value = typeof route.query.product === "string" ? route.query.product : "";
  if (!focus.value) return;
  document.getElementById(anchorId(focus.value))?.scrollIntoView({ block: "center" });
}

async function save(row: Editable) {
  if (!row.draftEarliest) {
    ElMessage.warning("最早可完工日期要填写");
    return;
  }
  row.saving = true;
  try {
    const saved: WarehouseItem = await api.patchWarehouse({
      product: row.product,
      on_hand: row.draftOnHand,
      earliest_completion: row.draftEarliest,
    });
    Object.assign(row, saved, {
      draftOnHand: saved.on_hand,
      draftEarliest: saved.earliest_completion,
    });
    ElMessage.success(
      saved.order_id ? "供应已更新，这个商品下的未关闭订单已按新数字重算" : "供应已更新",
    );
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    row.saving = false;
  }
}

watch(creating, (open) => {
  if (!open) return;
  form.product = "";
  form.onHand = 0;
  form.earliest = "";
  form.note = "";
});

function onMore(command: string | number | object, row: Editable) {
  if (command === "edit") openEdit(row);
  if (command === "delete") void removeProduct(row);
}

function openEdit(row: Editable) {
  editForm.product = row.product;
  editForm.name = row.product;
  editForm.onHand = row.on_hand;
  editForm.earliest = row.earliest_completion;
  editForm.note = row.note || "";
  editing.value = true;
}

async function saveEdit() {
  const name = editForm.name.trim();
  if (!name) {
    ElMessage.warning("先填写名称/规格");
    return;
  }
  if (editForm.onHand == null || Number.isNaN(editForm.onHand)) {
    ElMessage.warning("在手数量要填写");
    return;
  }
  if (!editForm.earliest) {
    ElMessage.warning("最早可完工日期要填写");
    return;
  }
  savingEdit.value = true;
  try {
    const saved: WarehouseItem = await api.patchWarehouse({
      product: editForm.product,
      name,
      on_hand: editForm.onHand,
      earliest_completion: editForm.earliest,
      note: editForm.note.trim(),
    });
    ElMessage.success("商品资料已更新");
    editing.value = false;
    await load(saved.product);
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    savingEdit.value = false;
  }
}

async function removeProduct(row: Editable) {
  try {
    await ElMessageBox.confirm(
      `删除仓库里的「${row.product}」。如果还有订单在用这个商品，会停下来并说明是哪些订单。`,
      "删除商品",
      { confirmButtonText: "删除", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  try {
    await api.deleteWarehouse(row.product);
    ElMessage.success("已从仓库删除");
    await load();
  } catch (error) {
    ElMessage.error(errorMessage(error));
  }
}

async function createProduct() {
  const product = form.product.trim();
  if (!product) {
    ElMessage.warning("先填写商品名称");
    return;
  }
  if (form.onHand == null || Number.isNaN(form.onHand)) {
    ElMessage.warning("在手数量要填写");
    return;
  }
  if (!form.earliest) {
    ElMessage.warning("最早可完工日期要填写");
    return;
  }
  savingProduct.value = true;
  try {
    const saved: WarehouseItem = await api.createWarehouse({
      product,
      on_hand: form.onHand,
      earliest_completion: form.earliest,
      note: form.note.trim(),
    });
    ElMessage.success("商品已加入仓库");
    creating.value = false;
    await load(saved.product);
  } catch (error) {
    ElMessage.error(errorMessage(error));
  } finally {
    savingProduct.value = false;
  }
}

watch(() => route.query.product, () => reveal());
watch(
  () => store.revision,
  () => {
    void load();
  },
);
onMounted(load);
</script>

<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h1>仓库</h1>
        <p>同一商品的在手数量是各客户共用的库存，不按客户拆仓。已占用是未关闭订单的未交数量，还可承诺 = 在手 − 已占用。待确认和已确认占用库存；待确认取消、已取消、已关闭不占用。未确认的客户要求不占库存。在手和最早可完工日期可以在行内保存，名称/规格和备注从「更多」里修改。</p>
      </div>
      <el-button type="primary" @click="creating = true">新增商品</el-button>
    </header>
    <div class="panel warehouse-wrap">
      <div v-if="!rows.length" class="empty">还没有商品。点右上角「新增商品」即可加入仓库。</div>
      <table v-else class="sheet warehouse">
        <thead>
          <tr>
            <th>商品</th>
            <th>在手</th>
            <th>已占用</th>
            <th>还可承诺</th>
            <th>最早可完工日期</th>
            <th>更新人 / 时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.product" :id="anchorId(row.product)" :class="{ focused: row.product === focus }">
            <td>
              <strong>{{ row.product }}</strong>
              <div class="muted" v-if="row.note">{{ row.note }}</div>
            </td>
            <td>
              <el-input-number v-model="row.draftOnHand" :min="0" :step="1" controls-position="right" />
            </td>
            <td>{{ row.reserved }} 件</td>
            <td :class="{ danger: row.committable < 0 }">{{ row.committable }} 件</td>
            <td>
              <el-date-picker v-model="row.draftEarliest" type="date" value-format="YYYY-MM-DD" placeholder="最早可完工日期" />
            </td>
            <td class="muted">{{ stamp(row) }}</td>
            <td>
              <div class="warehouse-actions">
                <el-button type="primary" :loading="row.saving" @click="save(row)">保存</el-button>
                <el-dropdown trigger="click" @command="(command: string | number | object) => onMore(command, row)">
                  <el-button>更多</el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="edit">修改</el-dropdown-item>
                      <el-dropdown-item command="delete" class="menu-danger">删除</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <el-dialog v-model="creating" title="新增商品" width="640px" :close-on-click-modal="false">
      <p class="muted dialog-lead">商品写入仓库后即可在列表里改在手数量和最早可完工日期。已有同名供应时不会另建一条。</p>
      <el-form label-position="top" class="form-grid" @submit.prevent="createProduct">
        <el-form-item label="商品" required>
          <el-input v-model="form.product" maxlength="120" placeholder="例如 密封圈" />
        </el-form-item>
        <el-form-item label="在手数量" required>
          <el-input-number v-model="form.onHand" :min="0" :step="1" controls-position="right" />
        </el-form-item>
        <el-form-item label="最早可完工日期" required>
          <el-date-picker v-model="form.earliest" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" />
        </el-form-item>
        <el-form-item label="供应说明" class="form-span">
          <el-input v-model="form.note" type="textarea" :rows="2" maxlength="500" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="creating = false">取消</el-button>
        <el-button type="primary" :loading="savingProduct" @click="createProduct">加入仓库</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="editing" title="修改商品" width="640px" :close-on-click-modal="false">
      <p class="muted dialog-lead">可以改名称/规格、在手数量、最早可完工日期和备注。不会改订单数量、订单交期、状态或客户。还有订单在用这个商品时，不能改名称。</p>
      <el-form label-position="top" class="form-grid" @submit.prevent="saveEdit">
        <el-form-item label="名称/规格" required>
          <el-input v-model="editForm.name" maxlength="120" />
        </el-form-item>
        <el-form-item label="在手数量" required>
          <el-input-number v-model="editForm.onHand" :min="0" :step="1" controls-position="right" />
        </el-form-item>
        <el-form-item label="最早可完工日期" required>
          <el-date-picker v-model="editForm.earliest" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" />
        </el-form-item>
        <el-form-item label="备注" class="form-span">
          <el-input v-model="editForm.note" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editing = false">取消</el-button>
        <el-button type="primary" :loading="savingEdit" @click="saveEdit">保存修改</el-button>
      </template>
    </el-dialog>
  </section>
</template>
