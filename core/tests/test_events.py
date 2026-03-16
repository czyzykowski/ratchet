from core.events import TASK_AUTO_MERGE_FAILED


def test_task_auto_merge_failed_constant_value():
    assert TASK_AUTO_MERGE_FAILED == "task.auto_merge_failed"
