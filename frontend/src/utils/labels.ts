export const eventLabels: Record<string, string> = {
  quantity_change: "数量变更",
  delivery_change: "交期变更",
  commitment: "承诺",
  cancellation: "取消",
  owner_unclear: "责任人不明",
  inventory_fact: "库存事实",
  production_constraint: "生产约束",
  partial_delivery: "部分交付",
  additional_order: "建议为该客户新增商品订单",
  offline_meeting: "线下洽谈",
  clarification: "待澄清",
  repeat_spec: "沿用上次规格",
};

export const riskLabels: Record<string, string> = {
  qty_over_stock: "数量冲突",
  delivery_before_capacity: "交期冲突",
  unverified_commitment: "承诺未核实",
  contradiction: "前后矛盾",
  owner_missing: "责任人缺失",
  cancellation: "取消待确认",
};

export const actionLabels: Record<string, string> = {
  create: "新建任务",
  update: "更新任务",
  close: "关闭任务",
};

export const importKindOptions = [
  { label: "聊天记录", value: "chat" },
  { label: "文件", value: "file" },
];

export const sourceLabels: Record<string, string> = {
  chat: "聊天记录",
  table: "表格",
  call_transcript: "通话转写",
  note: "备注",
  document: "文件",
};

export const fieldLabels: Record<string, string> = {
  quantity: "数量",
  delivery_date: "交期",
  available_qty: "在手数量",
  earliest_completion: "最早可完工日期",
  delivered_qty: "已交付",
  status: "状态",
};

export const taskStatuses = ["待处理", "进行中", "已完成", "已关闭"];
export const openBoardStatuses = ["待确认", "待处理", "进行中"];
export const doneTaskStatuses = ["已完成", "已关闭"];

export function labelOf(map: Record<string, string>, key: string | null | undefined) {
  if (!key) return "";
  return map[key] || key;
}
