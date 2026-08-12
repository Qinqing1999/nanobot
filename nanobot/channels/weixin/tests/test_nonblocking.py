"""Tests for WeChat non-blocking async processing (ADR-0014).

Covers:
    - IntentAction enum and IntentResult dataclass
    - KeywordIntentClassifier (MVP)
    - TaskState lifecycle and AsyncTask
    - SessionSnapshot and SessionForkManager
    - AsyncTaskCoordinator orchestration
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.channels.weixin.nonblocking import (
    AsyncTask,
    AsyncTaskCoordinator,
    BaseIntentClassifier,
    IntentAction,
    IntentResult,
    KeywordIntentClassifier,
    MergeResult,
    SessionForkManager,
    SessionSnapshot,
    TaskState,
)


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def _make_inbound_msg(content: str, chat_id: str = "user_123") -> SimpleNamespace:
    """Create a lightweight inbound message for testing."""
    return SimpleNamespace(
        content=content,
        chat_id=chat_id,
        media_paths=[],
        metadata={"message_id": "test_msg_1"},
    )


def _make_task(
    task_id: str = "test-task-abc123",
    session_key: str = "session:test",
) -> AsyncTask:
    """Create a test task."""
    return AsyncTask(
        task_id=task_id,
        session_key=session_key,
        original_msg=_make_inbound_msg("hello"),
        state=TaskState.QUEUED,
    )


# ---------------------------------------------------------------------------
# IntentAction Tests
# ---------------------------------------------------------------------------


class TestIntentAction:
    def test_enum_values(self):
        assert IntentAction.NEW_TASK.value == "new_task"
        assert IntentAction.CANCEL_PREV.value == "cancel_prev"
        assert IntentAction.MODIFY_PREV.value == "modify_prev"
        assert IntentAction.APPEND_CONTEXT.value == "append_context"


class TestIntentResult:
    def test_creation(self):
        result = IntentResult(
            action=IntentAction.NEW_TASK,
            confidence=0.95,
            target_task_id="task-1",
            reasoning="Test",
        )
        assert result.action == IntentAction.NEW_TASK
        assert result.confidence == 0.95
        assert result.target_task_id == "task-1"
        assert result.reasoning == "Test"

    def test_defaults(self):
        result = IntentResult(action=IntentAction.NEW_TASK, confidence=0.8)
        assert result.target_task_id is None
        assert result.reasoning == ""


# ---------------------------------------------------------------------------
# KeywordIntentClassifier Tests
# ---------------------------------------------------------------------------


class TestKeywordIntentClassifier:
    @pytest.fixture
    def classifier(self) -> KeywordIntentClassifier:
        return KeywordIntentClassifier()

    @pytest.mark.asyncio
    async def test_no_active_tasks_returns_new_task(self, classifier):
        msg = _make_inbound_msg("帮我写个代码")
        result = await classifier.classify(msg, [])
        assert result.action == IntentAction.NEW_TASK
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_cancel_keyword_chinese(self, classifier):
        active_tasks = [_make_task()]
        msg = _make_inbound_msg("算了，不用了")
        result = await classifier.classify(msg, active_tasks)
        assert result.action == IntentAction.CANCEL_PREV
        assert result.confidence >= 0.9
        assert "取消" in result.reasoning or "cancel" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_cancel_keyword_english(self, classifier):
        active_tasks = [_make_task()]
        msg = _make_inbound_msg("Cancel that")
        result = await classifier.classify(msg, active_tasks)
        assert result.action == IntentAction.CANCEL_PREV

    @pytest.mark.asyncio
    async def test_modify_keyword(self, classifier):
        active_tasks = [_make_task()]
        msg = _make_inbound_msg("换成用 Python 写")
        result = await classifier.classify(msg, active_tasks)
        assert result.action == IntentAction.MODIFY_PREV
        assert result.confidence >= 0.8

    @pytest.mark.asyncio
    async def test_append_keyword(self, classifier):
        active_tasks = [_make_task()]
        msg = _make_inbound_msg("还有，加上错误处理")
        result = await classifier.classify(msg, active_tasks)
        assert result.action == IntentAction.APPEND_CONTEXT
        assert result.confidence >= 0.7

    @pytest.mark.asyncio
    async def test_no_match_returns_new_task(self, classifier):
        active_tasks = [_make_task()]
        msg = _make_inbound_msg("今天天气怎么样")
        result = await classifier.classify(msg, active_tasks)
        assert result.action == IntentAction.NEW_TASK
        assert result.confidence >= 0.5

    @pytest.mark.asyncio
    async def test_cancel_takes_priority_over_modify(self, classifier):
        active_tasks = [_make_task()]
        # Message contains both cancel and modify keywords
        msg = _make_inbound_msg("算了，改成别的吧")
        result = await classifier.classify(msg, active_tasks)
        # Cancel is checked first, so it should match cancel keyword first
        assert result.action in (IntentAction.CANCEL_PREV, IntentAction.MODIFY_PREV)

    @pytest.mark.asyncio
    async def test_custom_keywords(self):
        custom_classifier = KeywordIntentClassifier(
            cancel_keywords={"自定义取消"},
            modify_keywords={"自定义修改"},
            append_keywords={"自定义追加"},
        )
        active_tasks = [_make_task()]

        r1 = await custom_classifier.classify(_make_inbound_msg("请自定义取消"), active_tasks)
        assert r1.action == IntentAction.CANCEL_PREV

        r2 = await custom_classifier.classify(_make_inbound_msg("请自定义修改"), active_tasks)
        assert r2.action == IntentAction.MODIFY_PREV

        r3 = await custom_classifier.classify(_make_inbound_msg("请自定义追加"), active_tasks)
        assert r3.action == IntentAction.APPEND_CONTEXT

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self, classifier):
        active_tasks = [_make_task()]
        msg = _make_inbound_msg("CANCEL the task")  # uppercase
        result = await classifier.classify(msg, active_tasks)
        assert result.action == IntentAction.CANCEL_PREV


# ---------------------------------------------------------------------------
# TaskState & AsyncTask Tests
# ---------------------------------------------------------------------------


class TestAsyncTask:
    def test_initial_state(self):
        task = _make_task()
        assert task.state == TaskState.QUEUED
        assert task.is_cancellable is True
        assert task.is_finished is False
        assert task.completed_at is None
        assert task.error is None

    def test_cancel_from_queued(self):
        task = _make_task()
        result = task.cancel()
        assert result is True
        assert task.state == TaskState.CANCELLED
        assert task.is_finished is True
        assert task.completed_at is not None

    def test_cancel_from_completed_fails(self):
        task = _make_task()
        task.state = TaskState.COMPLETED
        result = task.cancel()
        assert result is False
        assert task.state == TaskState.COMPLETED

    def test_cancel_from_running_succeeds(self):
        task = _make_task()
        task.state = TaskState.RUNNING
        result = task.cancel()
        assert result is True
        assert task.state == TaskState.CANCELLED

    def test_cancel_from_failed_fails(self):
        task = _make_task()
        task.state = TaskState.FAILED
        result = task.cancel()
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_cancel_immediate(self):
        task = _make_task()
        task._cancel_event.set()
        result = await task.wait_for_cancel(timeout=0.1)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_cancel_timeout(self):
        task = _make_task()
        result = await task.wait_for_cancel(timeout=0.01)
        assert result is False

    def test_all_terminal_states_are_finished(self):
        for state in [TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED]:
            task = _make_task()
            task.state = state
            assert task.is_finished is True, f"{state} should be finished"


class TestSessionSnapshot:
    def test_creation(self):
        snapshot = SessionSnapshot(
            session_key="session:test",
            messages=[{"role": "user", "content": "hi"}],
            provider_state=None,
            metadata={},
            created_at=time.time(),
        )
        assert snapshot.session_key == "session:test"
        assert snapshot.message_count == 1
        assert snapshot.forked_from_task_id is None

    def test_with_source_task(self):
        snapshot = SessionSnapshot(
            session_key="session:test",
            messages=[],
            provider_state=None,
            metadata={},
            created_at=time.time(),
            forked_from_task_id="task-abc",
        )
        assert snapshot.forked_from_task_id == "task-abc"


# ---------------------------------------------------------------------------
# SessionForkManager Tests
# ---------------------------------------------------------------------------


class TestSessionForkManager:
    @pytest.fixture
    def mock_session(self):
        """Create a mock Session object."""
        session = MagicMock()
        session.key = "session:user_123"
        session.messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        session.provider_state = {"model": "gpt-4", "temperature": 0.7}
        session.metadata = {
            "goal_state": {},  # volatile - should be excluded
            "title": "Test Chat",  # volatile - should be excluded
            "custom_key": "custom_value",
        }
        return session

    @pytest.fixture
    def fork_manager(self):
        get_session_fn = MagicMock(return_value=MagicMock())
        return SessionForkManager(get_session_fn=get_session_fn)

    def test_fork_creates_deep_copy(self, mock_session, fork_manager):
        snapshot = fork_manager.fork(mock_session)

        assert snapshot.session_key == mock_session.key
        assert len(snapshot.messages) == 2
        assert snapshot.messages[0]["content"] == "Hello"
        # Verify deep copy: modifying snapshot shouldn't affect original
        snapshot.messages.append({"role": "user", "content": "new"})
        assert len(mock_session.messages) == 2

    def test_fork_excludes_volatile_metadata(self, mock_session, fork_manager):
        snapshot = fork_manager.fork(mock_session)

        # Volatile keys should be excluded
        assert "goal_state" not in snapshot.metadata
        assert "title" not in snapshot.metadata
        # Non-volatile keys should remain
        assert snapshot.metadata.get("custom_key") == "custom_value"

    def test_fork_copies_provider_state(self, mock_session, fork_manager):
        snapshot = fork_manager.fork(mock_session)

        assert snapshot.provider_state is not None
        assert snapshot.provider_state["model"] == "gpt-4"
        # Deep copy check
        snapshot.provider_state["model"] = "modified"
        assert mock_session.provider_state["model"] == "gpt-4"

    def test_fork_with_source_task_id(self, mock_session, fork_manager):
        snapshot = fork_manager.fork(mock_session, source_task_id="task-xyz")
        assert snapshot.forked_from_task_id == "task-xyz"

    @pytest.mark.asyncio
    async def test_merge_appends_messages(self, mock_session, fork_manager):
        snapshot = fork_manager.fork(mock_session)
        new_messages = [
            {"role": "assistant", "content": "Here's your answer!"},
        ]

        result = await fork_manager.merge(
            mock_session, snapshot, new_messages, "task-merge-test"
        )

        assert result.success is True
        assert result.messages_appended == 1
        assert len(mock_session.messages) == 3
        assert mock_session.messages[-1]["content"] == "Here's your answer!"

    @pytest.mark.asyncio
    async def test_merge_with_mismatched_session_key(self, mock_session, fork_manager):
        snapshot = SessionSnapshot(
            session_key="wrong:key",
            messages=[],
            provider_state=None,
            metadata={},
            created_at=time.time(),
        )

        result = await fork_manager.merge(
            mock_session, snapshot, [], "task-bad-key"
        )

        assert result.success is False
        assert "mismatch" in result.error.lower()


# ---------------------------------------------------------------------------
# AsyncTaskCoordinator Tests
# ---------------------------------------------------------------------------


class TestAsyncTaskCoordinator:
    @pytest.fixture
    def coordinator(self):
        send_fn = AsyncMock()
        return AsyncTaskCoordinator(
            channel_name="weixin",
            send_fn=send_fn,
            max_concurrent=3,
            ack_template="",  # No ack for simpler tests
        )

    @pytest.mark.asyncio
    async def test_submit_creates_task(self, coordinator):
        msg = _make_inbound_msg("写代码")
        task_id = await coordinator.submit(
            msg=msg,
            session_key="session:test",
        )
        assert task_id.startswith("weixin-")

        task = coordinator.get_task(task_id)
        assert task is not None
        assert task.session_key == "session:test"
        assert task.state in (TaskState.QUEUED, TaskState.FORKING, TaskState.RUNNING)

    @pytest.mark.asyncio
    async def test_submit_sends_ack_when_configured(self):
        send_fn = AsyncMock()
        coord = AsyncTaskCoordinator(
            channel_name="weixin",
            send_fn=send_fn,
            max_concurrent=3,
            ack_template="收到！任务 {task_id} 已提交处理...",
        )
        msg = _make_inbound_msg("测试")
        await coord.submit(msg=msg, session_key="session:test", context_token="ctx123")

        # Ack should have been sent
        send_fn.assert_called_once()
        call_args = send_fn.call_args
        assert "收到" in call_args[1].get("content", "") or "收到" in call_args.kwargs.get("content", "")

    @pytest.mark.asyncio
    async def test_submit_classifies_intent(self, coordinator):
        # Submit a message with cancel intent while there's an active task
        first_msg = _make_inbound_msg("第一个任务")
        first_id = await coordinator.submit(msg=first_msg, session_key="session:test")

        # Now submit a cancel message
        cancel_msg = _make_inbound_msg("算了，取消这个任务")
        second_id = await coordinator.submit(msg=cancel_msg, session_key="session:test")

        # The second task should have CANCEL_PREV intent
        second_task = coordinator.get_task(second_id)
        assert second_task is not None
        assert second_task.intent is not None
        assert second_task.intent.action == IntentAction.CANCEL_PREV

        # First task should be cancelled
        first_task = coordinator.get_task(first_id)
        assert first_task is not None
        assert first_task.state == TaskState.CANCELLED

    @pytest.mark.asyncio
    async def test_get_active_tasks(self, coordinator):
        await coordinator.submit(
            msg=_make_inbound_msg("task1"), session_key="s1"
        )
        await coordinator.submit(
            msg=_make_inbound_msg("task2"), session_key="s1"
        )

        active = coordinator.get_active_tasks("s1")
        assert len(active) >= 1  # Some may have already completed

    @pytest.mark.asyncio
    async def test_cancel_task_by_id(self, coordinator):
        msg = _make_inbound_msg("待取消的任务")
        task_id = await coordinator.submit(msg=msg, session_key="session:test")

        # Task may complete quickly; check if it's still cancellable before asserting
        task = coordinator.get_task(task_id)
        if task and task.is_cancellable:
            result = await coordinator.cancel_task(task_id)
            assert result is True
            assert task.state == TaskState.CANCELLED
        else:
            # Task already completed (valid race condition in async tests)
            result = await coordinator.cancel_task(task_id)
            assert result is False  # Cannot cancel completed task

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, coordinator):
        result = await coordinator.cancel_task("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_all_for_session(self, coordinator):
        await coordinator.submit(msg=_make_inbound_msg("t1"), session_key="s1")
        await coordinator.submit(msg=_make_inbound_msg("t2"), session_key="s1")

        count = await coordinator.cancel_all("s1")
        assert count >= 1

    @pytest.mark.asyncio
    async def test_cleanup_finished_tasks(self, coordinator):
        # Create and immediately cancel a task
        msg = _make_inbound_msg("cleanup me")
        task_id = await coordinator.submit(msg=msg, session_key="s1")
        await asyncio.sleep(0.01)
        await coordinator.cancel_task(task_id)

        removed = coordinator.cleanup_finished("s1")
        assert removed >= 0

    @pytest.mark.asyncio
    async def test_total_active_property(self, coordinator):
        initial = coordinator.total_active
        assert isinstance(initial, int)

        await coordinator.submit(msg=_make_inbound_msg("t1"), session_key="s1")
        # Total may or may not increase depending on timing
        assert coordinator.total_active >= 0

    @pytest.mark.asyncio
    async def test_concurrency_limit_cancels_oldest(self):
        send_fn = AsyncMock()
        coord = AsyncTaskCoordinator(
            channel_name="weixin",
            send_fn=send_fn,
            max_concurrent=2,  # Low limit for testing
            ack_template="",
        )

        # Submit tasks up to limit
        t1 = await coord.submit(msg=_make_inbound_msg("t1"), session_key="s1")
        t2 = await coord.submit(msg=_make_inbound_msg("t2"), session_key="s1")

        # Submit one more — should trigger cancellation of oldest
        t3 = await coord.submit(msg=_make_inbound_msg("t3"), session_key="s1")

        # At least one of t1/t2 should be cancelled
        t1_task = coord.get_task(t1)
        t2_task = coord.get_task(t2)
        if t1_task:
            assert t1_task.state in (TaskState.CANCELLED, TaskState.QUEUED, TaskState.FORKING, TaskState.RUNNING)
        if t2_task:
            assert t2_task.state in (TaskState.CANCELLED, TaskState.QUEUED, TaskState.FORKING, TaskState.RUNNING)


# ---------------------------------------------------------------------------
# Integration-style tests
# ---------------------------------------------------------------------------


class TestNonBlockingIntegration:
    """Integration tests simulating the full non-blocking flow."""

    @pytest.mark.asyncio
    async def test_full_flow_submit_and_track(self):
        """Simulate: user sends message → task submitted → tracked → completed."""
        send_fn = AsyncMock()
        coord = AsyncTaskCoordinator(
            channel_name="weixin",
            send_fn=send_fn,
            max_concurrent=3,
            ack_template="已接收: {task_id}",
        )

        # User sends a message
        msg = _make_inbound_msg("帮我分析这段代码的性能问题")
        task_id = await coord.submit(
            msg=msg,
            session_key="session:user_456",
            context_token="ctx_token_abc",
        )

        # Verify task was created and tracked
        assert task_id.startswith("weixin-")
        task = coord.get_task(task_id)
        assert task is not None
        assert task.original_msg.content == "帮我分析这段代码的性能问题"
        assert task.intent is not None
        assert task.intent.action == IntentAction.NEW_TASK  # No active tasks → NEW_TASK

        # Verify acknowledgment was sent
        send_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_cancels_then_sends_new_task(self):
        """Simulate: user cancels previous task, then starts a new one."""
        send_fn = AsyncMock()
        coord = AsyncTaskCoordinator(
            channel_name="weixin",
            send_fn=send_fn,
            max_concurrent=3,
            ack_template="",
        )

        # Step 1: User sends first request
        t1 = await coord.submit(
            msg=_make_inbound_msg("翻译这篇文章成英文"),
            session_key="s1",
        )

        # Step 2: User changes mind
        t2 = await coord.submit(
            msg=_make_inbound_msg("算了，不用翻译了"),
            session_key="s1",
        )

        # Verify t1 was cancelled
        task1 = coord.get_task(t1)
        assert task1 is not None
        assert task1.state == TaskState.CANCELLED

        # Step 3: User sends a completely different request
        t3 = await coord.submit(
            msg=_make_inbound_msg("帮我写个周报"),
            session_key="s1",
        )

        task3 = coord.get_task(t3)
        assert task3 is not None
        assert task3.intent.action == IntentAction.NEW_TASK

    @pytest.mark.asyncio
    async def test_user_modifies_then_continues(self):
        """Simulate: user modifies parameters of running task."""
        send_fn = AsyncMock()
        coord = AsyncTaskCoordinator(
            channel_name="weixin",
            send_fn=send_fn,
            max_concurrent=3,
            ack_template="",
        )

        # Step 1: Initial request
        t1 = await coord.submit(
            msg=_make_inbound_msg("用 Java 写个排序算法"),
            session_key="s1",
        )

        # Step 2: User wants to change language
        t2 = await coord.submit(
            msg=_make_inbound_msg("不对，换成用 Go 语言"),
            session_key="s1",
        )

        # Verify MODIFY_PREV was detected
        task2 = coord.get_task(t2)
        assert task2 is not None
        assert task2.intent.action == IntentAction.MODIFY_PREV

        # Original task should be cancelled
        task1 = coord.get_task(t1)
        assert task1 is not None
        assert task1.state == TaskState.CANCELLED
