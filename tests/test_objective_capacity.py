from concurrent.futures import ThreadPoolExecutor
import threading

import pytest


def test_host_capacity_is_shared_between_independent_project_workers(tmp_path):
    from opaihub.objective_capacity import host_slot

    entered = threading.Event()
    stop = threading.Event()

    def competing_project():
        with host_slot(stop, directory=tmp_path, limit=1):
            entered.set()

    with host_slot(threading.Event(), directory=tmp_path, limit=1):
        with ThreadPoolExecutor(1) as pool:
            task = pool.submit(competing_project)
            assert not entered.wait(0.1)
            stop.set()
            with pytest.raises(InterruptedError):
                task.result(timeout=3)
    with host_slot(threading.Event(), directory=tmp_path, limit=1):
        pass


def test_host_slot_released_after_worker_failure(tmp_path):
    from opaihub.objective_capacity import host_slot

    with pytest.raises(RuntimeError):
        with host_slot(threading.Event(), directory=tmp_path, limit=1):
            raise RuntimeError("failed worker")
    with host_slot(threading.Event(), directory=tmp_path, limit=1):
        pass
