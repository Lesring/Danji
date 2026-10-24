<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { RouterLink, RouterView } from "vue-router";
import AssistantDrawer from "./components/AssistantDrawer.vue";
import EvidenceDrawer from "./components/EvidenceDrawer.vue";
import { useWorkspaceStore } from "./stores/workspace";

const store = useWorkspaceStore();
const railOpen = ref(localStorage.getItem("danji-rail-open") !== "0");
const assistantOpen = ref(false);

watch(railOpen, (open) => {
  localStorage.setItem("danji-rail-open", open ? "1" : "0");
});

onMounted(() => {
  store.loadHealth().catch(() => {
    store.extractMode = "fixture";
  });
});
</script>

<template>
  <div class="shell" :class="{ 'rail-collapsed': !railOpen }">
    <aside class="rail" :class="{ collapsed: !railOpen }">
      <div class="brand">
        <button
          type="button"
          class="mark"
          :aria-expanded="railOpen"
          :title="railOpen ? '收起侧栏' : '展开侧栏'"
          @click="railOpen = !railOpen"
        >
          迹
        </button>
        <div v-show="railOpen">
          <strong>单迹</strong>
          <small>订单协同</small>
        </div>
      </div>
      <nav>
        <RouterLink to="/" title="工作台" data-short="工">工作台</RouterLink>
        <RouterLink to="/tasks" title="任务看板" data-short="任">任务看板</RouterLink>
        <RouterLink to="/warehouse" title="仓库" data-short="仓">仓库</RouterLink>
      </nav>
      <div class="rail-foot">
        <button
          type="button"
          class="rail-toggle"
          :aria-expanded="assistantOpen"
          title="对话"
          @click="assistantOpen = !assistantOpen"
        >
          {{ railOpen ? "对话" : "话" }}
        </button>
        <p class="mode" v-if="railOpen && store.extractMode === 'fixture'">
          当前使用本地夹具解析，不会调用模型。密钥和接口留在 backend/.env，填好后把 EXTRACT_MODE 改为 llm。
        </p>
        <p class="mode" v-else-if="railOpen">
          当前使用模型解析{{ store.llmConfigured ? "" : "，但还没有填写密钥" }}。
        </p>
        <button type="button" class="rail-toggle" @click="railOpen = !railOpen">
          {{ railOpen ? "收起" : "展开" }}
        </button>
      </div>
    </aside>
    <main>
      <RouterView />
    </main>
    <EvidenceDrawer />
    <AssistantDrawer v-model="assistantOpen" />
  </div>
</template>
