import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exporter import DockerSwarmExporter  # noqa: E402


class FakeService:
    def __init__(self, attrs):
        self.attrs = attrs


def _bare_exporter():
    """Instance non initialisée (pas de connexion Docker) : les méthodes
    testées ici ne lisent que leurs arguments, jamais `self`."""
    return DockerSwarmExporter.__new__(DockerSwarmExporter)


def test_update_status_value_mapping():
    e = _bare_exporter()
    assert e._get_update_status_value("completed") == 1.0
    assert e._get_update_status_value("rollback_completed") == 1.0
    assert e._get_update_status_value("updating") == 0.5
    assert e._get_update_status_value("rollback_started") == 0.5
    assert e._get_update_status_value("paused") == 0.0
    assert e._get_update_status_value("failed") == 0.0
    assert e._get_update_status_value("rollback_paused") == 0.0
    assert e._get_update_status_value("something_unexpected") == 0.0


def test_mode_label_known_modes():
    e = _bare_exporter()
    assert e._get_mode_label("Replicated") == "replicated"
    assert e._get_mode_label("Global") == "global"
    assert e._get_mode_label("ReplicatedJob") == "replicated-job"
    assert e._get_mode_label("GlobalJob") == "global-job"


def test_mode_label_unknown_or_none():
    e = _bare_exporter()
    assert e._get_mode_label(None) == "unknown"
    assert e._get_mode_label("SomeFutureMode") == "unknown"


def test_get_service_mode_detection():
    e = _bare_exporter()
    service = FakeService({"Spec": {"Mode": {"ReplicatedJob": {}}}})
    assert e._get_service_mode(service) == "ReplicatedJob"


def test_get_service_mode_unrecognized():
    e = _bare_exporter()
    service = FakeService({"Spec": {"Mode": {"SomeFutureMode": {}}}})
    assert e._get_service_mode(service) is None


def test_current_replicas_counts_running_tasks_only():
    e = _bare_exporter()
    tasks = [
        {"Status": {"State": "running"}},
        {"Status": {"State": "complete"}},
        {"Status": {"State": "Running"}},
    ]
    assert e._get_current_replicas(tasks) == 2


def test_target_replicas_replicated_reads_spec():
    e = _bare_exporter()
    service = FakeService({"Spec": {"Mode": {"Replicated": {"Replicas": 4}}}})
    assert e._get_target_replicas(service, "Replicated", service_status={}) == 4


def test_target_replicas_global_reads_service_status():
    e = _bare_exporter()
    service = FakeService({"Spec": {"Mode": {"Global": {}}}})
    assert e._get_target_replicas(service, "Global", service_status={"DesiredTasks": 7}) == 7


def test_job_target_tasks_replicated_job_uses_total_completions():
    e = _bare_exporter()
    service = FakeService(
        {"Spec": {"Mode": {"ReplicatedJob": {"MaxConcurrent": 2, "TotalCompletions": 5}}}}
    )
    assert e._get_job_target_tasks(service, "ReplicatedJob", tasks=[]) == 5


def test_job_target_tasks_replicated_job_falls_back_to_max_concurrent():
    e = _bare_exporter()
    service = FakeService({"Spec": {"Mode": {"ReplicatedJob": {"MaxConcurrent": 2}}}})
    assert e._get_job_target_tasks(service, "ReplicatedJob", tasks=[]) == 2


def test_job_target_tasks_global_job_counts_distinct_nodes():
    e = _bare_exporter()
    tasks = [{"NodeID": "node-a"}, {"NodeID": "node-b"}, {"NodeID": "node-a"}]
    assert e._get_job_target_tasks(None, "GlobalJob", tasks) == 2
