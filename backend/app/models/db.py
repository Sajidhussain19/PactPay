"""Database models (SQLAlchemy 2.0).

Conventions: string UUID primary keys (portable across SQLite and Postgres), money as
Numeric(12, 2), timestamps in UTC, flexible payloads (evidence, reports, raw PayPal
responses) as JSON.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.agents.contract_schema import CheckType
from app.workflows.milestone_state import MilestoneState


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


Money = Numeric(12, 2)


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    # Store enum *values* ("funded"), not names, as plain strings: readable and portable.
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class UserRole(StrEnum):
    CLIENT = "client"
    FREELANCER = "freelancer"
    ADMIN = "admin"


class ContractStatus(StrEnum):
    DRAFT = "draft"
    AGREED = "agreed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"))
    paypal_email: Mapped[str | None] = mapped_column(String(320))  # where payouts go
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContractRecord(Base):
    __tablename__ = "contracts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3))
    total: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[ContractStatus] = mapped_column(
        _enum(ContractStatus, "contract_status"), default=ContractStatus.DRAFT
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    freelancer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    client_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freelancer_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mockup_path: Mapped[str | None] = mapped_column(String(500))
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    milestones: Mapped[list["MilestoneRecord"]] = relationship(
        back_populates="contract", order_by="MilestoneRecord.position", cascade="all, delete-orphan"
    )
    client: Mapped[User] = relationship(foreign_keys=[client_id])
    freelancer: Mapped[User | None] = relationship(foreign_keys=[freelancer_id])

    @property
    def is_agreed(self) -> bool:
        return bool(self.client_approved_at and self.freelancer_approved_at)


class MilestoneRecord(Base):
    __tablename__ = "milestones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    code: Mapped[str] = mapped_column(String(10))  # "M1"
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    amount: Mapped[Decimal] = mapped_column(Money)
    due_date: Mapped[date] = mapped_column(Date)
    state: Mapped[MilestoneState] = mapped_column(
        _enum(MilestoneState, "milestone_state"), default=MilestoneState.DRAFT, index=True
    )
    # PayPal references. PactPay never holds money; these point at PayPal's records.
    paypal_order_id: Mapped[str | None] = mapped_column(String(64))
    paypal_authorization_id: Mapped[str | None] = mapped_column(String(64), index=True)
    authorization_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authorization_voided: Mapped[bool] = mapped_column(default=False)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payout_batch_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payout_status: Mapped[str | None] = mapped_column(String(32))
    passed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    # Optimistic locking: concurrent updates of the same milestone fail instead of racing.
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": version}

    contract: Mapped[ContractRecord] = relationship(back_populates="milestones")
    criteria: Mapped[list["CriterionRecord"]] = relationship(
        back_populates="milestone",
        order_by="CriterionRecord.position",
        cascade="all, delete-orphan",
    )
    submissions: Mapped[list["SubmissionRecord"]] = relationship(
        back_populates="milestone", order_by="SubmissionRecord.attempt"
    )

    @property
    def payout_succeeded(self) -> bool:
        return self.payout_status == "SUCCESS"


class CriterionRecord(Base):
    __tablename__ = "criteria"
    __table_args__ = (UniqueConstraint("milestone_id", "code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("milestones.id"), index=True)
    code: Mapped[str] = mapped_column(String(20))  # "M1-C3"
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    check_type: Mapped[CheckType] = mapped_column(_enum(CheckType, "check_type"))
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_objective: Mapped[bool]
    rationale: Mapped[str] = mapped_column(Text, default="")

    milestone: Mapped[MilestoneRecord] = relationship(back_populates="criteria")


class SubmissionRecord(Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("milestone_id", "attempt"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("milestones.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    live_url: Mapped[str] = mapped_column(String(2000))
    repo_url: Mapped[str | None] = mapped_column(String(2000))
    notes: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    milestone: Mapped[MilestoneRecord] = relationship(back_populates="submissions")


class VerificationRunRecord(Base):
    __tablename__ = "verification_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("milestones.id"), index=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|completed|error
    overall: Mapped[str | None] = mapped_column(String(16))
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    results: Mapped[list["CheckResultRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class CheckResultRecord(Base):
    __tablename__ = "check_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("verification_runs.id"), index=True)
    criterion_code: Mapped[str] = mapped_column(String(20))
    check_type: Mapped[CheckType] = mapped_column(_enum(CheckType, "check_type"))
    verdict: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool | None]
    summary: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    fix_suggestion: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(16))
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)

    run: Mapped[VerificationRunRecord] = relationship(back_populates="results")


class PaymentEvent(Base):
    """Every money-related call we make or receive. `idempotency_key` is unique, so the
    same capture/payout can never be recorded (or executed) twice."""

    __tablename__ = "payment_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    milestone_id: Mapped[str | None] = mapped_column(ForeignKey("milestones.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # authorized, captured, payout_sent, ...
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    paypal_id: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32))
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEventRecord(Base):
    """Processed PayPal webhooks; the primary key makes dedupe atomic."""

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    """Append-only log of every state change and who caused it."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    contract_id: Mapped[str | None] = mapped_column(ForeignKey("contracts.id"), index=True)
    milestone_id: Mapped[str | None] = mapped_column(ForeignKey("milestones.id"), index=True)
    actor: Mapped[str] = mapped_column(String(64))  # user id, "system", "paypal", "verifier"
    action: Mapped[str] = mapped_column(String(64))
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str | None] = mapped_column(String(32))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
