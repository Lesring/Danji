from typing import Literal

from pydantic import BaseModel, Field


class ConfirmIn(BaseModel):
    kind: Literal["event", "task"]
    apply_to_order: bool = False
    proposed_value: str | None = None
    owner: str | None = None
    due_date: str | None = None
    title: str | None = None
    operator: str = "演示用户"


class RejectIn(BaseModel):
    kind: Literal["event", "task"]
    reason: str = ""
    operator: str = "演示用户"


class TaskPatch(BaseModel):
    title: str | None = None
    owner: str | None = None
    due_date: str | None = None
    status: str | None = None
    operator: str = "演示用户"


class IntakeIn(BaseModel):
    text: str
    title: str = ""


class ChatPreviewIn(BaseModel):
    text: str


class SourceIn(BaseModel):
    type: Literal["chat", "table", "call_transcript", "note", "document"] = "chat"
    title: str = ""
    text: str = ""
    business_time: str | None = None
    file_name: str | None = None


class OrderFields(BaseModel):
    order_no: str
    product: str = ""
    quantity: int
    delivery_date: str
    owner: str = "订单负责人"
    available_qty: int
    earliest_completion: str
    supply_note: str = ""


class CustomerIn(BaseModel):
    name: str
    contact: str = ""
    note: str = ""
    order: OrderFields | None = None


class OrderIn(OrderFields):
    customer_id: int


class SupplyPatch(BaseModel):
    on_hand: int
    earliest_completion: str
    operator: str = "演示用户"


class WarehousePatch(BaseModel):
    product: str
    on_hand: int
    earliest_completion: str
    note: str | None = None
    name: str | None = None
    operator: str = "演示用户"


class WarehouseCreate(BaseModel):
    product: str
    on_hand: int
    earliest_completion: str
    note: str = ""
    operator: str = "演示用户"


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""


class AssistantTurnIn(BaseModel):
    route: Literal["workbench", "review", "tasks", "warehouse"] = "workbench"
    order_id: int | None = None
    product: str | None = None
    messages: list[AssistantMessage] = Field(default_factory=list)
