import os

from save_manager.bootstrap_lock import InstanceLock


def test_instance_lock_is_exclusive(tmp_path):
    path = tmp_path / "instance.lock"
    first, second = InstanceLock(path), InstanceLock(path)
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_process_liveness_check_preserves_current_process():
    assert InstanceLock._pid_is_alive(os.getpid())
