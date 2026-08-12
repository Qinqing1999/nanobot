"""Non-blocking async processing for WeChat channel.

Implements the async task coordinator, intent classifier, and session fork/merge
mechanism as specified in ADR-0014 and the weixin-nonblocking-async spec.

Core components:
    - IntentAction / IntentResult: Intent classification enums and data classes
    - TaskState / AsyncTask: Task lifecycle management
    - SessionSnapshot: Immutable session state at fork point
    - IntentClassifier: LLM-driven (or keyword) intent classification
    - SessionForkManager: Session fork and merge operations
    - AsyncTaskCoordinator: High-level task orchestration
"""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from nanobot.channels.weixin.nonblocking import AsyncTask

from loguru import logger

# ---------------------------------------------------------------------------
# Intent Classification
# ---------------------------------------------------------------------------


class IntentAction(str, Enum):
    """Classification result for a new message relative to active tasks."""

    NEW_TASK = "new_task"  # Independent new task
    CANCEL_PREV = "cancel_prev"  # User wants to cancel the previous task
    MODIFY_PREV = "modify_prev"  # User wants to modify parameters of previous task
    APPEND_CONTEXT = "append_context"  # User is adding context to previous task


# Keywords for each intent category (used by KeywordIntentClassifier)
_CANCEL_KEYWORDS = {
    "算了", "取消", "不用了", "cancel", "stop", "不要了", "罢了吧",
    "不管了", "跳过", "忽略", "forget it", "never mind",
}

_MODIFY_KEYWORDS = {
    "换成", "改成", "不对，用", "不是，是", "重新", "换个",
    "等一下", "等等", "wait", "change to", "instead use", "modify",
    "更正", "修正", "纠正", "correct",
}

_APPEND_KEYWORDS = {
    "还有", "另外", "再加上", "以及", "也", "补充",
    "and also", "additionally", "plus", "also", "moreover",
}


@dataclass
class IntentResult:
    """Result of intent classification."""

    action: IntentAction
    confidence: float  # 0.0 - 1.0
    target_task_id: str | None = None  # For CANCEL_PREV/MODIFY_PREV
    reasoning: str = ""  # Human-readable explanation (for logging)


class BaseIntentClassifier:
    """Abstract base for intent classifiers."""

    async def classify(
        self,
        new_message_content: str,
        active_tasks: list[AsyncTask],  # type: ignore[name-defined]
    ) -> IntentResult:
        """Classify the intent of a new message relative to active tasks."""
        raise NotImplementedError


class KeywordIntentClassifier(BaseIntentClassifier):
    """Keyword-based intent classifier (MVP implementation).

    Uses pattern matching on common Chinese/English cancel/modify/append phrases.
    No LLM call required — fast and deterministic.
    """

    def __init__(
        self,
        cancel_keywords: set[str] | None = None,
        modify_keywords: set[str] | None = None,
        append_keywords: set[str] | None = None,
    ) -> None:
        self._cancel_keywords = cancel_keywords or _CANCEL_KEYWORDS
        self._modify_keywords = modify_keywords or _MODIFY_KEYWORDS
        self._append_keywords = append_keywords or _APPEND_KEYWORDS

    async def classify(
        self,
        new_message_content: str,
        active_tasks: list[AsyncTask],  # type: ignore[name-defined]
    ) -> IntentResult:
        text = new_message_content.content.strip().lower() if hasattr(new_message_content, 'content') else str(new_message_content).lower()

        # No active tasks → always NEW_TASK
        if not active_tasks:
            return IntentResult(
                action=IntentAction.NEW_TASK,
                confidence=1.0,
                reasoning="No active tasks",
            )

        # Get the most recent active task
        latest_task = active_tasks[-1]

        # Check cancel keywords first (highest priority)
        for kw in self._cancel_keywords:
            if kw in text:
                return IntentResult(
                    action=IntentAction.CANCEL_PREV,
                    confidence=0.9,
                    target_task_id=latest_task.task_id,
                    reasoning=f"Matched cancel keyword: {kw!r}",
                )

        # Check modify keywords
        for kw in self._modify_keywords:
            if kw in text:
                return IntentResult(
                    action=IntentAction.MODIFY_PREV,
                    confidence=0.85,
                    target_task_id=latest_task.task_id,
                    reasoning=f"Matched modify keyword: {kw!r}",
                )

        # Check append keywords
        for kw in self._append_keywords:
            if kw in text:
                return IntentResult(
                    action=IntentAction.APPEND_CONTEXT,
                    confidence=0.8,
                    target_task_id=latest_task.task_id,
                    reasoning=f"Matched append keyword: {kw!r}",
                )

        # Default: new independent task
        return IntentResult(
            action=IntentAction.NEW_TASK,
            confidence=0.7,
            reasoning="No intent keywords matched",
        )


# ---------------------------------------------------------------------------
# Task State Machine
# ---------------------------------------------------------------------------


class TaskState(str, Enum):
    """Async task lifecycle states."""

    QUEUED = "queued"
    FORKING = "forking"
    RUNNING = "running"
    MERGING = "merging"
    PUSHING = "pushing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class SessionSnapshot:
    """Immutable snapshot of a Session at the point of fork.

    Created when a new async task is started, used as the isolated
    context for that task's execution.
    """

    session_key: str
    messages: list[dict[str, Any]]
    provider_state: Any | None
    metadata: dict[str, Any]
    created_at: float
    forked_from_task_id: str | None = None

    @property
    def message_count(self) -> int:
        return len(self.messages)


@dataclass
class AsyncTask:
    """Represents an async processing task."""

    task_id: str
    session_key: str
    original_msg: Any  # InboundMessage (avoid circular import)
    session_snapshot: SessionSnapshot | None = None
    intent: IntentResult | None = None
    state: TaskState = TaskState.QUEUED
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    result: Any | None = None  # OutboundMessage
    error: str | None = None
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_cancellable(self) -> bool:
        """Check if this task can still be cancelled."""
        return self.state in (TaskState.QUEUED, TaskState.FORKING, TaskState.RUNNING)

    @property
    def is_finished(self) -> bool:
        """Check if this task has reached a terminal state."""
        return self.state in (
            TaskState.COMPLETED,
            TaskState.CANCELLED,
            TaskState.FAILED,
        )

    def cancel(self) -> bool:
        """Request cancellation of this task.

        Returns True if cancellation was requested, False if already finished.
        """
        if not self.is_cancellable:
            return False
        self._cancel_event.set()
        self.state = TaskState.CANCELLED
        self.completed_at = time.time()
        logger.info("Task {} cancelled", self.task_id)
        return True

    async def wait_for_cancel(self, timeout: float | None = None) -> bool:
        """Wait until cancellation is requested or timeout."""
        try:
            await asyncio.wait_for(self._cancel_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


# ---------------------------------------------------------------------------
# Session Fork Manager
# ---------------------------------------------------------------------------


@dataclass
class MergeResult:
    """Result of a session merge operation."""

    success: bool
    messages_appended: int = 0
    conflict_detected: bool = False
    error: str | None = None


class SessionForkManager:
    """Manages Session forks and merges for async task processing.

    Implements the Append-All merge strategy: all completed task results
    are appended to the main session in completion order.
    """

    def __init__(
        self,
        get_session_fn: Callable[[str], Any],  # session_key -> Session
        volatile_metadata_keys: set[str] | None = None,
    ) -> None:
        """
        Args:
            get_session_fn: Callable that returns a Session object by session_key
            volatile_metadata_keys: Set of metadata keys to exclude from snapshots
        """
        self._get_session = get_session_fn
        self._volatile_keys = volatile_metadata_keys or {
            "goal_state",
            "pending_user_turn",
            "runtime_checkpoint",
            "thread_goal",
            "title",
            "title_user_edited",
        }

    def fork(self, session: Any, source_task_id: str | None = None) -> SessionSnapshot:
        """Create an immutable snapshot of a Session for async execution.

        Performs deep copy of messages and provider_state, but excludes
        volatile metadata fields that are turn-specific.
        """
        # Deep copy messages list
        messages_copy = copy.deepcopy(getattr(session, 'messages', []))

        # Deep copy provider_state if present
        provider_state = getattr(session, 'provider_state', None)
        provider_state_copy = copy.deepcopy(provider_state) if provider_state else None

        # Copy metadata, excluding volatile keys
        metadata = dict(getattr(session, 'metadata', {}))
        metadata = {k: v for k, v in metadata.items() if k not in self._volatile_keys}

        snapshot = SessionSnapshot(
            session_key=session.key,
            messages=messages_copy,
            provider_state=provider_state_copy,
            metadata=metadata,
            created_at=time.time(),
            forked_from_task_id=source_task_id,
        )

        logger.debug(
            "Forked session {} with {} messages (task={})",
            session.key,
            snapshot.message_count,
            source_task_id or "none",
        )
        return snapshot

    async def merge(
        self,
        session: Any,
        snapshot: SessionSnapshot,
        result_messages: list[dict[str, Any]],
        task_id: str,
    ) -> MergeResult:
        """Merge task results back into the main session.

        Append-All strategy:
        - All result_messages are appended to session.messages
        - Provider state is updated if newer
        - Session is saved after merge

        Args:
            session: The live Session object to merge into
            snapshot: The original snapshot (for version checking)
            result_messages: New messages to append (assistant replies)
            task_id: The completed task ID (for logging)

        Returns:
            MergeResult with status information
        """
        try:
            # Verify session key matches
            if session.key != snapshot.session_key:
                return MergeResult(
                    success=False,
                    error=f"Session key mismatch: {session.key} != {snapshot.session_key}",
                )

            # Append result messages
            current_len = len(session.messages)
            for msg in result_messages:
                # Ensure timestamp on new messages
                if isinstance(msg, dict) and 'timestamp' not in msg:
                    from datetime import datetime
                    msg['timestamp'] = datetime.now().isoformat()
                session.messages.append(msg)

            appended = len(session.messages) - current_len

            # Save session
            if hasattr(session, 'save'):
                session.save()

            logger.info(
                "Merged {} messages from task {} into session {}",
                appended,
                task_id,
                session.key,
            )

            return MergeResult(
                success=True,
                messages_appended=appended,
            )

        except Exception as e:
            logger.exception("Failed to merge task {} into session {}", task_id, session.key)
            return MergeResult(
                success=False,
                error=str(e),
            )


# ---------------------------------------------------------------------------
# Async Task Coordinator
# ---------------------------------------------------------------------------


class AsyncTaskCoordinator:
    """Per-channel async task coordinator.

    Orchestrates the full lifecycle of non-blocking async tasks:
    1. Receive new message
    2. Classify intent (via IntentClassifier)
    3. Handle intent (cancel/modify/append as needed)
    4. Fork Session
    5. Execute task asynchronously
    6. Merge results back
    7. Push response to user
    """

    def __init__(
        self,
        channel_name: str,
        send_fn: Callable[..., Any],  # Async function to send messages
        max_concurrent: int = 3,
        classifier: BaseIntentClassifier | None = None,
        fork_manager: SessionForkManager | None = None,
        ack_template: str = "",
    ) -> None:
        """
        Args:
            channel_name: Channel identifier (e.g., "weixin")
            send_fn: Async callable for sending responses to user
            max_concurrent: Max parallel tasks per user/session
            classifier: Intent classifier instance (default: KeywordIntentClassifier)
            fork_manager: Session fork/merge manager
            ack_template: Template for acknowledgment messages (empty = no ack)
        """
        self.channel_name = channel_name
        self._send_fn = send_fn
        self._max_concurrent = max_concurrent
        self._classifier = classifier or KeywordIntentClassifier()
        self._fork_manager = fork_manager
        self._ack_template = ack_template

        # Per-session-key active tasks (ordered by creation time)
        self._active_tasks: dict[str, list[AsyncTask]] = {}
        # Task index for quick lookup
        self._task_index: dict[str, AsyncTask] = {}

    @property
    def classifier(self) -> BaseIntentClassifier:
        return self._classifier

    async def submit(
        self,
        msg: Any,  # InboundMessage
        session_key: str,
        session: Any | None = None,
        context_token: str = "",
    ) -> str:
        """Submit a new async task.

        This is the main entry point for non-blocking message processing.
        Returns immediately with a task_id; actual processing happens in background.

        Args:
            msg: The inbound message
            session_key: Session identifier
            session: Live Session object (for forking)
            context_token: WeChat context token for this chat

        Returns:
            task_id: Unique identifier for the tracking task
        """
        # Generate task ID
        task_id = f"{self.channel_name}-{uuid.uuid4().hex[:12]}"

        # Classify intent
        active_for_session = self._active_tasks.get(session_key, [])
        intent = await self._classifier.classify(msg, active_for_session)

        logger.info(
            "[{}] Task {} intent: {} (confidence={:.2f})",
            self.channel_name,
            task_id[:12],
            intent.action.value,
            intent.confidence,
        )

        # Handle intent actions that affect existing tasks
        if intent.action in (IntentAction.CANCEL_PREV, IntentAction.MODIFY_PREV):
            await self._handle_cancel_or_modify(
                session_key, intent.target_task_id, intent.action
            )
        elif intent.action == IntentAction.APPEND_CONTEXT:
            # Append to existing task's pending queue (if still running)
            if active_for_session:
                latest = active_for_session[-1]
                if latest.is_cancellable:
                    logger.info(
                        "[{}] Appending context to task {}",
                        self.channel_name,
                        latest.task_id[:12],
                    )
                    # Note: Actual append-to-pending-queue is handled by AgentLoop's
                    # existing injection mechanism; here we just log and proceed as new task
                    # that will be merged later

        # Check concurrency limit
        running_count = sum(
            1 for t in active_for_session if not t.is_finished
        )
        if running_count >= self._max_concurrent:
            logger.warning(
                "[{}] Max concurrent ({}) reached for session {}, cancelling oldest",
                self.channel_name,
                self._max_concurrent,
                session_key,
            )
            # Cancel the oldest non-finished task
            for t in active_for_session:
                if t.is_cancellable:
                    t.cancel()
                    break

        # Create task
        task = AsyncTask(
            task_id=task_id,
            session_key=session_key,
            original_msg=msg,
            intent=intent,
            state=TaskState.QUEUED,
        )

        # Register task
        if session_key not in self._active_tasks:
            self._active_tasks[session_key] = []
        self._active_tasks[session_key].append(task)
        self._task_index[task_id] = task

        # Fork session if manager available
        if self._fork_manager and session:
            try:
                task.session_snapshot = self._fork_manager.fork(
                    session, source_task_id=task_id
                )
                task.state = TaskState.FORKING
            except Exception:
                logger.exception("[{}] Failed to fork session for task {}", self.channel_name, task_id[:12])

        # Send acknowledgment if configured
        if self._ack_template:
            try:
                ack_msg = self._ack_template.format(task_id=task_id[:8])
                await self._send_fn(
                    chat_id=msg.chat_id if hasattr(msg, 'chat_id') else session_key,
                    content=ack_msg,
                    context_token=context_token,
                )
            except Exception:
                logger.debug("[{}] Failed to send acknowledgment", self.channel_name)

        # Start async execution
        asyncio.create_task(self._execute_task(task, session, context_token))

        return task_id

    async def _handle_cancel_or_modify(
        self,
        session_key: str,
        target_task_id: str | None,
        action: IntentAction,
    ) -> None:
        """Handle CANCEL_PREV or MODIFY_PREV intent on an existing task."""
        if not target_task_id:
            # Cancel the most recent active task for this session
            tasks = self._active_tasks.get(session_key, [])
            for t in reversed(tasks):
                if t.is_cancellable:
                    t.cancel()
                    logger.info(
                        "[{}] {} task {} due to intent",
                        self.channel_name,
                        action.value,
                        t.task_id[:12],
                    )
                    return
            return

        task = self._task_index.get(target_task_id)
        if task and task.cancel():
            logger.info(
                "[{}] {} task {} (targeted)",
                self.channel_name,
                action.value,
                task.task_id[:12],
            )

    async def _execute_task(
        self,
        task: AsyncTask,
        session: Any | None,
        context_token: str,
    ) -> None:
        """Execute a task asynchronously: publish to bus, wait for result, merge, push."""

        if task.state == TaskState.CANCELLED:
            return

        task.state = TaskState.RUNNING
        start_time = time.time()

        try:
            # The actual execution happens via MessageBus → AgentLoop pipeline
            # This is coordinated by WeixinChannel._process_nonblocking()
            # Here we just manage the state transitions

            elapsed = time.time() - start_time
            logger.info(
                "[{}] Task {} completed in {:.2f}s",
                self.channel_name,
                task.task_id[:12],
                elapsed,
            )

            task.state = TaskState.COMPLETED
            task.completed_at = time.time()

        except asyncio.CancelledError:
            task.state = TaskState.CANCELLED
            task.completed_at = time.time()
            logger.info("[{}] Task {} cancelled", self.channel_name, task.task_id[:12])

        except Exception as e:
            task.state = TaskState.FAILED
            task.completed_at = time.time()
            task.error = str(e)
            logger.exception(
                "[{}] Task {} failed: {}",
                self.channel_name,
                task.task_id[:12],
                e,
            )

    def get_active_tasks(self, session_key: str) -> list[AsyncTask]:
        """Return active (non-finished) tasks for a session."""
        return [t for t in self._active_tasks.get(session_key, []) if not t.is_finished]

    def get_task(self, task_id: str) -> AsyncTask | None:
        """Look up a task by ID."""
        return self._task_index.get(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a specific task by ID."""
        task = self._task_index.get(task_id)
        if task:
            return task.cancel()
        return False

    async def cancel_all(self, session_key: str) -> int:
        """Cancel all active tasks for a session. Returns count of cancelled tasks."""
        tasks = self._active_tasks.get(session_key, [])
        count = 0
        for task in tasks:
            if task.cancel():
                count += 1
        return count

    def cleanup_finished(self, session_key: str | None = None) -> int:
        """Remove finished tasks from tracking. Returns count removed."""
        if session_key:
            tasks = self._active_tasks.get(session_key, [])
            before = len(tasks)
            self._active_tasks[session_key] = [t for t in tasks if not t.is_finished]
            # Update index
            finished_ids = {t.task_id for t in tasks if t.is_finished}
            for tid in finished_ids:
                self._task_index.pop(tid, None)
            return before - len(self._active_tasks[session_key])
        else:
            # Cleanup all sessions
            total = 0
            for key in list(self._active_tasks.keys()):
                total += self.cleanup_finished(key)
            return total

    @property
    def total_active(self) -> int:
        """Total number of non-finished tasks across all sessions."""
        return sum(
            len(self.get_active_tasks(key))
            for key in self._active_tasks
        )

    def __repr__(self) -> str:
        return (
            f"AsyncTaskCoordinator(channel={self.channel_name!r}, "
            f"active={self.total_active}, "
            f"sessions={len(self._active_items)})"
        )

    @property
    def _active_items(self) -> list[str]:
        return [k for k, v in self._active_tasks.items() if any(not t.is_finished for t in v)]
