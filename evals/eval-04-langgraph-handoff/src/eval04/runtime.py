"""MOD-04 LangGraph StateGraph Runtime and stateless Worker wrapper for EVAL-04."""

from __future__ import annotations

import json
from typing import Any, TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from eval04.checkpointer import OrbiPostgresSaver
from eval04.db import open_connection
from eval04.gateway import ToolCallIntent, ToolExecutionGateway
from eval04.models import (
    InteractionStatus,
    InteractionType,
    ReceiptStatus,
    SimulatedCrashError,
    TaskNotResumableError,
    TaskStatus,
    TurnResult,
    UncertainExecutionEscalation,
    WaitingReason,
)
from eval04.task_manager import ClaimedResumeEvent, DeterministicTaskManager


class OrbiGraphState(TypedDict, total=False):
    """Intra-turn LangGraph scratchpad (subordinate to PostgreSQL orbi_app)."""

    task_id: str
    execution_id: str
    tenant_id: str
    owner_principal: str
    goal: str
    requires_hitl: bool
    hitl_type: str
    hitl_prompt: dict[str, Any]
    response_schema: dict[str, Any]
    allowed_response_channels: list[str]
    tool_id: str | None
    tool_args: dict[str, Any]
    target_resource: str
    logical_step_id: str
    approval_id: str | None
    human_response: dict[str, Any] | None
    tool_output: dict[str, Any] | None
    final_answer: str | None
    steps_executed: list[str]
    cancelled_after_external_commit: bool


class LangGraphWorkerRuntime:
    """MOD-04 bounded-turn LangGraph runtime backed by OrbiPostgresSaver."""

    def __init__(
        self,
        *,
        saver: OrbiPostgresSaver,
        task_manager: DeterministicTaskManager,
        gateway: ToolExecutionGateway,
    ) -> None:
        self.saver = saver
        self.task_manager = task_manager
        self.gateway = gateway
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        builder = StateGraph(OrbiGraphState)
        builder.add_node("plan_step", self._plan_step)
        builder.add_node("interaction_or_approval_step", self._interaction_or_approval_step)
        builder.add_node("execute_tool_step", self._execute_tool_step)
        builder.add_node("conclude_step", self._conclude_step)

        builder.add_edge(START, "plan_step")
        builder.add_conditional_edges(
            "plan_step",
            self._route_after_plan,
            {
                "hitl": "interaction_or_approval_step",
                "tool": "execute_tool_step",
                "conclude": "conclude_step",
            },
        )
        builder.add_conditional_edges(
            "interaction_or_approval_step",
            self._route_after_hitl,
            {
                "tool": "execute_tool_step",
                "conclude": "conclude_step",
            },
        )
        builder.add_conditional_edges(
            "execute_tool_step",
            self._route_after_tool,
            {
                "conclude": "conclude_step",
                "stop": END,
            },
        )
        builder.add_edge("conclude_step", END)
        return builder.compile(checkpointer=self.saver)

    def _plan_step(self, state: OrbiGraphState) -> dict[str, Any]:
        steps = list(state.get("steps_executed") or [])
        steps.append("plan_step")
        return {"steps_executed": steps}

    def _route_after_plan(self, state: OrbiGraphState) -> str:
        if state.get("requires_hitl"):
            return "hitl"
        if state.get("tool_id"):
            return "tool"
        return "conclude"

    def _route_after_hitl(self, state: OrbiGraphState) -> str:
        if state.get("tool_id"):
            return "tool"
        return "conclude"

    def _route_after_tool(self, state: OrbiGraphState) -> str:
        if state.get("cancelled_after_external_commit"):
            return "stop"
        return "conclude"

    def _interaction_or_approval_step(
        self, state: OrbiGraphState, config: RunnableConfig
    ) -> dict[str, Any]:
        cfg = config["configurable"]
        task_id = UUID(state["task_id"])
        worker_id: str = cfg["worker_id"]
        claim_epoch: int = int(cfg["claim_epoch"])

        hitl_type = InteractionType(
            state.get("hitl_type", InteractionType.INFORMATION_REQUEST.value)
        )
        waiting_reason = (
            WaitingReason.APPROVAL_REQUEST
            if hitl_type == InteractionType.APPROVAL_REQUEST
            else WaitingReason.INFORMATION_REQUEST
        )

        approval_kwargs: dict[str, Any] = {}
        if hitl_type == InteractionType.APPROVAL_REQUEST:
            tool_id = state.get("tool_id")
            if not tool_id:
                raise ValueError("APPROVAL_REQUEST requires tool_id")
            tool_def = self.gateway.registry.get_tool(tool_id)
            approval_kwargs = {
                "approval_tool_id": tool_def.tool_id,
                "approval_tool_version": tool_def.tool_version,
                "approval_args": state.get("tool_args", {}),
                "approval_semantic_fields": tool_def.semantic_fields,
                "approval_target_resource": state.get("target_resource", "default"),
            }

        logical_key = f"hitl:{state['execution_id']}:step1"
        self.task_manager.prepare_suspension_if_needed(
            task_id=task_id,
            worker_id=worker_id,
            claim_epoch=claim_epoch,
            logical_interaction_key=logical_key,
            interaction_type=hitl_type,
            waiting_reason=waiting_reason,
            requesting_agent="agent:eval04",
            prompt_payload=state.get("hitl_prompt", {"question": "Input required"}),
            response_schema=state.get("response_schema", {"type": "object"}),
            allowed_response_channels=tuple(
                state.get("allowed_response_channels") or ["telegram", "dashboard"]
            ),
            **approval_kwargs,
        )

        # Yields via LangGraph GraphInterrupt on initial pass; returns resume value on resume
        resume_val = interrupt(state.get("hitl_prompt", {"question": "Input required"}))
        steps = list(state.get("steps_executed") or [])
        steps.append("interaction_or_approval_step")
        return {
            "human_response": resume_val,
            "approval_id": (
                str(cfg["approval_id"]) if cfg.get("approval_id") else state.get("approval_id")
            ),
            "steps_executed": steps,
        }

    def _execute_tool_step(
        self, state: OrbiGraphState, config: RunnableConfig
    ) -> dict[str, Any]:
        cfg = config["configurable"]
        task_id = UUID(state["task_id"])
        execution_id = UUID(state["execution_id"])
        worker_id: str = cfg["worker_id"]
        claim_epoch: int = int(cfg["claim_epoch"])
        resume_token_id: UUID | None = cfg.get("resume_token_id")
        approval_id_raw = cfg.get("approval_id") or state.get("approval_id")
        approval_id = UUID(str(approval_id_raw)) if approval_id_raw else None

        tool_id = state["tool_id"]
        assert tool_id is not None
        args_to_use = (
            cfg["override_tool_args"]
            if cfg.get("override_tool_args") is not None
            else dict(state.get("tool_args") or {})
        )
        # Merge non-approval human response data if provided (e.g., INFORMATION_REQUEST answer)
        if (
            state.get("human_response")
            and state.get("hitl_type") != InteractionType.APPROVAL_REQUEST.value
            and "answer" in state["human_response"]
            and "user_choice" not in args_to_use
        ):
            args_to_use["user_choice"] = state["human_response"]["answer"]

        intent = ToolCallIntent(
            tenant_id=state["tenant_id"],
            actor_user_id=state["owner_principal"],
            task_id=task_id,
            execution_id=execution_id,
            logical_step_id=state.get("logical_step_id", "step-tool-1"),
            tool_id=tool_id,
            arguments=args_to_use,
            target_resource=state.get("target_resource", "default"),
            worker_id=worker_id,
            claim_epoch=claim_epoch,
            resume_token_id=resume_token_id,
            approval_id=approval_id,
        )

        try:
            result = self.gateway.execute_tool_intent(intent)
        except UncertainExecutionEscalation as exc:
            reconcile_key = f"reconcile:{exc.receipt_id}"
            reconcile_prompt = {
                "waiting_reason": WaitingReason.UNCERTAIN_EXECUTION_RECONCILIATION.value,
                "receipt_id": str(exc.receipt_id),
                "action_idempotency_key": exc.action_idempotency_key,
                "tool_id": exc.tool_id,
                "target_resource": exc.target_resource,
                "options": [
                    "CONFIRM_ALREADY_EXECUTED",
                    "AUTHORIZE_EXPLICIT_RETRY",
                    "ABORT_TASK",
                ],
            }
            reconcile_schema = {
                "type": "object",
                "properties": {
                    "reconciliation_action": {
                        "type": "string",
                        "enum": [
                            "CONFIRM_ALREADY_EXECUTED",
                            "AUTHORIZE_EXPLICIT_RETRY",
                            "ABORT_TASK",
                        ],
                    }
                },
                "required": ["reconciliation_action"],
            }
            self.task_manager.prepare_suspension_if_needed(
                task_id=task_id,
                worker_id=worker_id,
                claim_epoch=claim_epoch,
                logical_interaction_key=reconcile_key,
                interaction_type=InteractionType.INFORMATION_REQUEST,
                waiting_reason=WaitingReason.UNCERTAIN_EXECUTION_RECONCILIATION,
                requesting_agent="mod-08-gateway",
                prompt_payload=reconcile_prompt,
                response_schema=reconcile_schema,
            )
            decision_payload = interrupt(reconcile_prompt)
            action = decision_payload["reconciliation_action"]
            if action == "CONFIRM_ALREADY_EXECUTED":
                with open_connection(self.task_manager.dsn) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE orbi_app.step_execution_receipts
                            SET status = %s,
                                sanitized_output = %s::jsonb,
                                updated_at = now()
                            WHERE receipt_id = %s
                            """,
                            (
                                ReceiptStatus.CONFIRMED_SUCCESS.value,
                                json.dumps({"human_reconciled": "CONFIRM_ALREADY_EXECUTED"}),
                                exc.receipt_id,
                            ),
                        )
                        self.task_manager._audit(
                            cur,
                            task_id=task_id,
                            interaction_id=None,
                            event_type="EXTERNAL_OUTCOME_HUMAN_CONFIRMED",
                            actor=worker_id,
                            details={"receipt_id": str(exc.receipt_id)},
                        )
                    conn.commit()
                steps = list(state.get("steps_executed") or [])
                steps.append("execute_tool_step:human_confirmed")
                return {
                    "tool_output": {"human_reconciled": "CONFIRM_ALREADY_EXECUTED"},
                    "cancelled_after_external_commit": False,
                    "steps_executed": steps,
                }
            if action == "AUTHORIZE_EXPLICIT_RETRY":
                with open_connection(self.task_manager.dsn) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE orbi_app.step_execution_receipts
                            SET status = %s, updated_at = now()
                            WHERE receipt_id = %s
                            """,
                            (
                                ReceiptStatus.CONFIRMED_FAILURE_SAFE_TO_RETRY.value,
                                exc.receipt_id,
                            ),
                        )
                        self.task_manager._audit(
                            cur,
                            task_id=task_id,
                            interaction_id=None,
                            event_type="EXTERNAL_ACTION_EXPLICIT_RETRY_AUTHORIZED",
                            actor=worker_id,
                            details={"receipt_id": str(exc.receipt_id)},
                        )
                    conn.commit()
                result = self.gateway.execute_tool_intent(intent)
            else:
                self.task_manager.cancel_task(
                    task_id, actor=worker_id, reason="aborted_during_uncertain_reconciliation"
                )
                raise TaskNotResumableError("Task aborted during uncertain execution reconciliation")

        steps = list(state.get("steps_executed") or [])
        steps.append("execute_tool_step")
        return {
            "tool_output": result.output,
            "cancelled_after_external_commit": result.cancelled_after_external_commit,
            "steps_executed": steps,
        }

    def _conclude_step(self, state: OrbiGraphState) -> dict[str, Any]:
        steps = list(state.get("steps_executed") or [])
        steps.append("conclude_step")
        answer = (
            f"Completed goal='{state.get('goal')}' "
            f"human={state.get('human_response')} "
            f"tool={state.get('tool_output')}"
        )
        return {
            "final_answer": answer,
            "steps_executed": steps,
        }


class Worker:
    """Stateless bounded-turn worker owning no cross-turn memory or sleeping threads."""

    def __init__(
        self,
        worker_id: str,
        *,
        task_manager: DeterministicTaskManager,
        gateway: ToolExecutionGateway,
        dsn: str | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.task_manager = task_manager
        self.gateway = gateway
        self.saver = OrbiPostgresSaver.create(dsn)
        self.runtime = LangGraphWorkerRuntime(
            saver=self.saver,
            task_manager=task_manager,
            gateway=gateway,
        )
        self.closed = False

    def close(self) -> None:
        """Completely release worker checkpointer connection and graph runtime references."""
        self.saver.close()
        self.closed = True

    def _find_pending_created_interaction(self, task_id: UUID) -> tuple[UUID, UUID | None]:
        with open_connection(self.task_manager.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT interaction_id, approval_id
                    FROM orbi_app.interaction_requests
                    WHERE task_id = %s AND status = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (task_id, InteractionStatus.CREATED.value),
                )
                row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"Expected CREATED interaction for suspending task {task_id}")
        return row["interaction_id"], row["approval_id"]

    def run_initial_turn(
        self,
        task_id: UUID,
        *,
        initial_state: OrbiGraphState,
        lease_ttl_seconds: float = 30.0,
        crash_before_phase_c: bool = False,
        crash_before_completion_commit: bool = False,
    ) -> TurnResult:
        """Claim a QUEUED task and run one bounded turn until completion or interrupt."""
        task = self.task_manager.claim_initial_task(
            task_id, worker_id=self.worker_id, lease_ttl_seconds=lease_ttl_seconds
        )
        full_state: OrbiGraphState = {
            **initial_state,
            "task_id": str(task.task_id),
            "execution_id": str(task.execution_id),
            "tenant_id": task.tenant_id,
            "owner_principal": task.owner_principal,
            "goal": task.goal,
        }
        config = {
            "configurable": {
                "thread_id": str(task.task_id),
                "worker_id": self.worker_id,
                "claim_epoch": task.claim_epoch,
                "resume_token_id": None,
                "approval_id": None,
            }
        }
        output = self.runtime.graph.invoke(full_state, config=config)
        return self._finalize_turn_output(
            task_id=task.task_id,
            execution_id=task.execution_id,
            claim_epoch=task.claim_epoch,
            output=output,
            outbox_event_id=None,
            resume_token_id=None,
            resumed=False,
            crash_before_phase_c=crash_before_phase_c,
            crash_before_completion_commit=crash_before_completion_commit,
        )

    def run_resumed_turn(
        self,
        claimed: ClaimedResumeEvent,
        *,
        override_tool_args: dict[str, Any] | None = None,
        crash_before_phase_c: bool = False,
        crash_before_completion_commit: bool = False,
        crash_after_processed_before_ack: bool = False,
    ) -> TurnResult:
        """Rehydrate from verified PostgreSQL-authorized LangGraph checkpoint and run bounded turn."""
        if not claimed.verified_checkpoint_id:
            raise TaskNotResumableError("Cannot resume without verified_checkpoint_id in PostgreSQL")
        if not self.saver.verify_checkpoint(
            str(claimed.task_id), claimed.verified_checkpoint_id, require_interrupt=True
        ):
            raise TaskNotResumableError(
                f"Checkpoint {claimed.verified_checkpoint_id} missing or lacks __interrupt__"
            )

        resume_payload = claimed.payload.get("response", claimed.payload)
        approval_id = claimed.payload.get("approval_id")
        config = {
            "configurable": {
                "thread_id": str(claimed.task_id),
                "checkpoint_ns": "",
                "checkpoint_id": claimed.verified_checkpoint_id,
                "worker_id": self.worker_id,
                "claim_epoch": claimed.claim_epoch,
                "resume_token_id": claimed.resume_token_id,
                "approval_id": approval_id,
                "override_tool_args": override_tool_args,
            }
        }
        output = self.runtime.graph.invoke(Command(resume=resume_payload), config=config)
        return self._finalize_turn_output(
            task_id=claimed.task_id,
            execution_id=claimed.execution_id,
            claim_epoch=claimed.claim_epoch,
            output=output,
            outbox_event_id=claimed.event_id,
            resume_token_id=claimed.resume_token_id,
            resumed=True,
            crash_before_phase_c=crash_before_phase_c,
            crash_before_completion_commit=crash_before_completion_commit,
            crash_after_processed_before_ack=crash_after_processed_before_ack,
        )

    def _finalize_turn_output(
        self,
        *,
        task_id: UUID,
        execution_id: UUID,
        claim_epoch: int,
        output: dict[str, Any],
        outbox_event_id: UUID | None,
        resume_token_id: UUID | None,
        resumed: bool,
        crash_before_phase_c: bool = False,
        crash_before_completion_commit: bool = False,
        crash_after_processed_before_ack: bool = False,
    ) -> TurnResult:
        if "__interrupt__" in output:
            ckpt_id = self.task_manager.phase_b_record_and_verify_checkpoint(
                task_id=task_id, saver=self.saver, require_interrupt=True
            )
            if crash_before_phase_c:
                raise SimulatedCrashError(
                    "Injected crash after Phase B checkpoint verification before Phase C PG commit"
                )
            interaction_id, approval_id = self._find_pending_created_interaction(task_id)
            self.task_manager.phase_c_commit_waiting_for_user(
                task_id=task_id,
                interaction_id=interaction_id,
                checkpoint_id=ckpt_id,
                actor_id=self.worker_id,
                claim_epoch=claim_epoch,
                outbox_event_id=outbox_event_id,
                resume_token_id=resume_token_id,
            )
            return TurnResult(
                worker_id=self.worker_id,
                task_id=task_id,
                execution_id=execution_id,
                task_status=TaskStatus.WAITING_FOR_USER,
                claim_epoch=claim_epoch,
                checkpoint_id=ckpt_id,
                interaction_id=interaction_id,
                approval_id=approval_id,
                resumed_from_checkpoint=resumed,
            )

        if output.get("cancelled_after_external_commit"):
            return TurnResult(
                worker_id=self.worker_id,
                task_id=task_id,
                execution_id=execution_id,
                task_status=TaskStatus.CANCELLED_AFTER_EXTERNAL_COMMIT,
                claim_epoch=claim_epoch,
                resumed_from_checkpoint=resumed,
            )

        if crash_before_completion_commit:
            raise SimulatedCrashError(
                "Injected crash after LangGraph run before PG completion commit"
            )

        # If this was a resumed turn that completed without suspending again, mark outbox processed
        ckpt_latest = self.saver.get_latest_checkpoint_id(str(task_id))
        completed_task = self.task_manager.complete_task_turn(
            task_id=task_id,
            worker_id=self.worker_id,
            claim_epoch=claim_epoch,
            result_payload={
                "final_answer": output.get("final_answer"),
                "tool_output": output.get("tool_output"),
                "steps_executed": output.get("steps_executed", []),
            },
            outbox_event_id=outbox_event_id,
            resume_token_id=resume_token_id,
            crash_after_processed_event_before_outbox_ack=crash_after_processed_before_ack,
        )
        return TurnResult(
            worker_id=self.worker_id,
            task_id=task_id,
            execution_id=execution_id,
            task_status=completed_task.status,
            claim_epoch=claim_epoch,
            checkpoint_id=ckpt_latest,
            final_answer=output.get("final_answer"),
            resumed_from_checkpoint=resumed,
        )
