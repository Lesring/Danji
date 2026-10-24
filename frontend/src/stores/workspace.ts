import { defineStore } from "pinia";
import { api } from "../api/client";

export const useWorkspaceStore = defineStore("workspace", {
  state: () => ({
    extractMode: "fixture",
    llmConfigured: false,
    evidenceOpen: false,
    evidenceId: null as number | null,
    excerpt: "",
    row: null as number | null,
    col: null as number | null,
    contextOrderId: null as number | null,
    revision: 0,
  }),
  actions: {
    async loadHealth() {
      const health = await api.health();
      this.extractMode = health.extract_mode;
      this.llmConfigured = health.llm_configured;
    },
    bumpRevision() {
      this.revision += 1;
    },
    openEvidence(sourceId: number | null, excerpt = "", row: number | null = null, col: number | null = null) {
      if (!sourceId) return;
      this.evidenceId = sourceId;
      this.excerpt = excerpt;
      this.row = row;
      this.col = col;
      this.evidenceOpen = true;
    },
  },
});
