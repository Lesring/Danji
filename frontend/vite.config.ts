import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const target = "http://127.0.0.1:8000";

function pageOrApi(req: { method?: string; headers: { accept?: string } }) {
  const accept = req.headers.accept || "";
  if (accept.includes("application/json")) return;
  if (req.method === "GET" && accept.includes("text/html")) {
    return "/index.html";
  }
}

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/customers": target,
      "/orders": { target, bypass: pageOrApi },
      "/drafts": target,
      "/tasks": { target, bypass: pageOrApi },
      "/warehouse": { target, bypass: pageOrApi },
      "/evidence": target,
      "/demo": target,
      "/health": target,
      "/assistant": target,
    },
  },
});
