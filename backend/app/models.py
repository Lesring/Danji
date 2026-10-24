from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    contact: Mapped[str] = mapped_column(String(120), default="")
    note: Mapped[str] = mapped_column(Text, default="")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    product: Mapped[str] = mapped_column(String(120), default="")
    quantity: Mapped[int] = mapped_column(Integer)
    delivered_qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    delivery_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="已确认")
    owner: Mapped[str] = mapped_column(String(80), default="订单负责人")
    deleted_at: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)


class SupplyConstraint(Base):
    __tablename__ = "supply_constraints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    available_qty: Mapped[int] = mapped_column(Integer)
    earliest_completion: Mapped[str] = mapped_column(String(10))
    note: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)


class ProductSupply(Base):
    __tablename__ = "product_supplies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[str] = mapped_column(String(120), unique=True)
    on_hand: Mapped[int] = mapped_column(Integer)
    earliest_completion: Mapped[str] = mapped_column(String(10))
    note: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    updated_at: Mapped[str] = mapped_column(String(32), default=now_iso)
    updated_by: Mapped[str] = mapped_column(String(80), default="系统")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    parsed_table_json: Mapped[str] = mapped_column(Text, default="")
    business_time: Mapped[str | None] = mapped_column(String(32), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)


class BusinessEvent(Base):
    __tablename__ = "business_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    event_type: Mapped[str] = mapped_column(String(40))
    field_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_value: Mapped[str | None] = mapped_column(String(80), nullable=True)
    proposed_value: Mapped[str | None] = mapped_column(String(80), nullable=True)
    proposal_origin: Mapped[str] = mapped_column(String(20), default="")
    evidence_excerpt: Mapped[str] = mapped_column(Text, default="")
    evidence_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_col: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    explanation: Mapped[str] = mapped_column(Text, default="")
    conflict_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    conflict_note: Mapped[str] = mapped_column(Text, default="")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    suggestion_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)


class Risk(Base):
    __tablename__ = "risks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    event_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_excerpt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)


class TaskSuggestion(Base):
    __tablename__ = "task_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    suggestion_key: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str] = mapped_column(String(80), default="")
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(20), default="create")
    target_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    order_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    event_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    order_no: Mapped[str] = mapped_column(String(40), default="")
    suggestion_key: Mapped[str] = mapped_column(String(60), default="")
    title: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str] = mapped_column(String(80), default="")
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="待处理")
    reason: Mapped[str] = mapped_column(Text, default="")
    event_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    suggestion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(32), default=now_iso)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(40))
    before_json: Mapped[str] = mapped_column(Text, default="")
    after_json: Mapped[str] = mapped_column(Text, default="")
    operator: Mapped[str] = mapped_column(String(80), default="演示用户")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)


class CustomerIntake(Base):
    __tablename__ = "customer_intakes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    title: Mapped[str] = mapped_column(String(200), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    summary_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    result_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)


class ExtractionLog(Base):
    __tablename__ = "extraction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    mode: Mapped[str] = mapped_column(String(20))
    snapshot_json: Mapped[str] = mapped_column(Text, default="")
    raw_output: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default=now_iso)
