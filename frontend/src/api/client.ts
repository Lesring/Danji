import axios from "axios";

export const http = axios.create();

export function errorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) return "请求失败";
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    return String(detail.message);
  }
  return "请求失败";
}

export const api = {
  health: () => http.get("/health").then((response) => response.data),
  customers: () => http.get("/customers").then((response) => response.data),
  createCustomer: (body: Record<string, unknown>) => http.post("/customers", body).then((response) => response.data),
  previewCustomer: (text: string) => http.post("/customers/preview", { text }).then((response) => response.data),
  previewCustomerFile: (form: FormData) => http.post("/customers/preview/file", form).then((response) => response.data),
  createOrders: (body: Record<string, unknown>) => http.post("/orders/batch", body).then((response) => response.data),
  orders: (params?: { customer_id?: number; product?: string; trashed?: boolean }) =>
    http.get("/orders", { params }).then((response) => response.data),
  createOrder: (body: Record<string, unknown>) => http.post("/orders", body).then((response) => response.data),
  deleteOrder: (id: number) => http.delete(`/orders/${id}`).then((response) => response.data),
  restoreOrder: (id: number) => http.post(`/orders/${id}/restore`).then((response) => response.data),
  purgeOrder: (id: number) => http.delete(`/orders/${id}/purge`).then((response) => response.data),
  order: (id: number) => http.get(`/orders/${id}`).then((response) => response.data),
  updateSupply: (id: number, body: { on_hand: number; earliest_completion: string }) =>
    http.patch(`/orders/${id}/supply`, body).then((response) => response.data),
  warehouse: () => http.get("/warehouse").then((response) => response.data),
  createWarehouse: (body: { product: string; on_hand: number; earliest_completion: string; note?: string }) =>
    http.post("/warehouse", body).then((response) => response.data),
  patchWarehouse: (body: {
    product: string;
    on_hand: number;
    earliest_completion: string;
    note?: string;
    name?: string;
  }) => http.patch("/warehouse", body).then((response) => response.data),
  deleteWarehouse: (product: string) =>
    http.delete("/warehouse", { params: { product } }).then((response) => response.data),
  drafts: (id: number) => http.get(`/orders/${id}/drafts`).then((response) => response.data),
  createSource: (orderId: number, body: Record<string, string>) =>
    http.post(`/orders/${orderId}/sources`, body).then((response) => response.data),
  uploadSource: (orderId: number, form: FormData) =>
    http.post(`/orders/${orderId}/sources`, form).then((response) => response.data),
  confirm: (id: number, body: Record<string, unknown>) =>
    http.post(`/drafts/${id}/confirm`, body).then((response) => response.data),
  reject: (id: number, body: Record<string, unknown>) =>
    http.post(`/drafts/${id}/reject`, body).then((response) => response.data),
  tasks: (params?: { order_id?: number; status?: string }) =>
    http.get("/tasks", { params }).then((response) => response.data),
  patchTask: (id: number, body: Record<string, unknown>) =>
    http.patch(`/tasks/${id}`, body).then((response) => response.data),
  evidence: (id: number) => http.get(`/evidence/${id}`).then((response) => response.data),
  assistantParseFile: (form: FormData) =>
    http.post("/assistant/parse-file", form).then((response) => response.data),
  assistantTurn: (body: {
    route: string;
    order_id?: number | null;
    product?: string | null;
    messages: { role: "user" | "assistant"; content: string }[];
    attachment?: {
      filename: string;
      source_type: string;
      title: string;
      text: string;
    } | null;
  }) => http.post("/assistant/turn", body).then((response) => response.data),
};
