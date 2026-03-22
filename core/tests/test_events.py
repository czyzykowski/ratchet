from core.events import TASK_ASSIGNED_TO_WORKER, TASK_AUTO_MERGE_FAILED


def test_task_auto_merge_failed_constant_value():
    assert TASK_AUTO_MERGE_FAILED == "task.auto_merge_failed"


def test_task_assigned_to_worker_constant_exists():
    assert TASK_ASSIGNED_TO_WORKER == "task.assigned_to_worker"
