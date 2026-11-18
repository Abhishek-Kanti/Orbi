"""Scoped LangGraph PostgreSQL checkpointer wrapper for EVAL-04."""

from __future__ import annotations

from typing import Any, Sequence

import psycopg
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata, RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

from eval04.db import get_database_url
from eval04.models import SimulatedCrashError


class OrbiPostgresSaver(PostgresSaver):
    """PostgresSaver isolated to the `langgraph_checkpoints` schema with verification & fault hooks."""

    def __init__(self, conn: psycopg.Connection, *, dsn: str | None = None) -> None:
        super().__init__(conn)
        self._dsn = dsn or get_database_url()
        self.fail_on_put: bool = False
        self.fail_on_put_writes_interrupt: bool = False
        self.last_checkpoint_id_by_thread: dict[str, str] = {}
        self.put_count: int = 0
        self.put_writes_count: int = 0

    @classmethod
    def create(cls, dsn: str | None = None, *, force_setup: bool = False) -> OrbiPostgresSaver:
        """Open a dedicated autocommit connection scoped to `langgraph_checkpoints` and run setup() if needed."""
        conn_dsn = dsn or get_database_url()
        conn = psycopg.connect(
            conn_dsn,
            autocommit=True,
            row_factory=dict_row,
            options="-c search_path=langgraph_checkpoints,public",
        )
        saver = cls(conn, dsn=conn_dsn)
        needs_setup = force_setup
        if not needs_setup:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('langgraph_checkpoints.checkpoints') AS tbl")
                row = cur.fetchone()
                needs_setup = row is None or row["tbl"] is None
        if needs_setup:
            saver.setup()
        return saver

    def close(self) -> None:
        if not self.conn.closed:
            self.conn.close()

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, str | int | float],
    ) -> RunnableConfig:
        if self.fail_on_put:
            raise SimulatedCrashError("Injected crash during LangGraph checkpoint put()")
        result_config = super().put(config, checkpoint, metadata, new_versions)
        self.put_count += 1
        configurable = result_config.get("configurable", {})
        thread_id = configurable.get("thread_id")
        checkpoint_id = configurable.get("checkpoint_id") or checkpoint.get("id")
        if thread_id and checkpoint_id:
            self.last_checkpoint_id_by_thread[str(thread_id)] = str(checkpoint_id)
        return result_config

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        has_interrupt = any(channel == "__interrupt__" for channel, _ in writes)
        if self.fail_on_put_writes_interrupt and has_interrupt:
            raise SimulatedCrashError(
                "Injected crash during LangGraph __interrupt__ put_writes()"
            )
        super().put_writes(config, writes, task_id, task_path)
        self.put_writes_count += 1
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")
        checkpoint_id = configurable.get("checkpoint_id")
        if thread_id and checkpoint_id:
            self.last_checkpoint_id_by_thread[str(thread_id)] = str(checkpoint_id)

    def get_latest_checkpoint_id(self, thread_id: str) -> str | None:
        """Read back the latest checkpoint_id directly from langgraph_checkpoints.checkpoints."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT checkpoint_id
                FROM langgraph_checkpoints.checkpoints
                WHERE thread_id = %s AND checkpoint_ns = ''
                ORDER BY checkpoint_id DESC
                LIMIT 1
                """,
                (thread_id,),
            )
            row = cur.fetchone()
            return str(row["checkpoint_id"]) if row else None

    def get_latest_interrupt_checkpoint_id(self, thread_id: str) -> str | None:
        """Find the latest checkpoint_id that has an __interrupt__ write in checkpoint_writes."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.checkpoint_id
                FROM langgraph_checkpoints.checkpoints AS c
                JOIN langgraph_checkpoints.checkpoint_writes AS w
                  ON c.thread_id = w.thread_id
                 AND c.checkpoint_ns = w.checkpoint_ns
                 AND c.checkpoint_id = w.checkpoint_id
                WHERE c.thread_id = %s
                  AND c.checkpoint_ns = ''
                  AND w.channel = '__interrupt__'
                ORDER BY c.checkpoint_id DESC
                LIMIT 1
                """,
                (thread_id,),
            )
            row = cur.fetchone()
            return str(row["checkpoint_id"]) if row else None

    def verify_checkpoint(
        self,
        thread_id: str,
        checkpoint_id: str,
        *,
        require_interrupt: bool = False,
    ) -> bool:
        """Read-back integrity check ensuring checkpoint (and __interrupt__ write if required) exists."""
        tup = self.get_tuple(
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint_id,
                }
            }
        )
        if tup is None:
            return False
        if require_interrupt:
            pending = getattr(tup, "pending_writes", None) or []
            if any(w[1] == "__interrupt__" for w in pending):
                return True
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM langgraph_checkpoints.checkpoint_writes
                    WHERE thread_id = %s
                      AND checkpoint_ns = ''
                      AND checkpoint_id = %s
                      AND channel = '__interrupt__'
                    LIMIT 1
                    """,
                    (thread_id, checkpoint_id),
                )
                return cur.fetchone() is not None
        return True

    def count_checkpoints_for_thread(self, thread_id: str) -> int:
        """Count stored checkpoint rows in langgraph_checkpoints for a thread_id."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS cnt FROM langgraph_checkpoints.checkpoints WHERE thread_id = %s",
                (thread_id,),
            )
            row = cur.fetchone()
            return int(row["cnt"]) if row else 0

    def purge_task_checkpoints(self, thread_id: str) -> int:
        """Remove all checkpoint rows/blobs/writes for a completed, cancelled, or rolled-back task."""
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM langgraph_checkpoints.checkpoint_writes WHERE thread_id = %s",
                (thread_id,),
            )
            cur.execute(
                "DELETE FROM langgraph_checkpoints.checkpoint_blobs WHERE thread_id = %s",
                (thread_id,),
            )
            cur.execute(
                "DELETE FROM langgraph_checkpoints.checkpoints WHERE thread_id = %s",
                (thread_id,),
            )
            return cur.rowcount or 0
