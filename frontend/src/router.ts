import { createRouter, createWebHistory } from "vue-router";
import Review from "./views/Review.vue";
import Tasks from "./views/Tasks.vue";
import Warehouse from "./views/Warehouse.vue";
import Workbench from "./views/Workbench.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "workbench", component: Workbench },
    { path: "/orders/:id/review", name: "review", component: Review },
    { path: "/tasks", name: "tasks", component: Tasks },
    { path: "/warehouse", name: "warehouse", component: Warehouse },
  ],
});

export default router;
