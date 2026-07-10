from conftest import metrics_for


def _value(samples, name, **labels):
    for s in samples:
        if s.name == name and all(s.labels.get(k) == v for k, v in labels.items()):
            return s.value
    return None


def test_replicated_job_metrics(test_environment, exporter):
    samples = metrics_for(exporter, test_environment["prefix"])
    name = test_environment["job_replicated"]

    assert _value(samples, "docker_swarm_service_job_tasks_running", service_name=name, mode="replicated-job") == 0.0
    assert _value(samples, "docker_swarm_service_job_tasks_completed", service_name=name, mode="replicated-job") == 3.0
    assert _value(samples, "docker_swarm_service_job_tasks_target", service_name=name, mode="replicated-job") == 3.0


def test_global_job_metrics(test_environment, exporter):
    samples = metrics_for(exporter, test_environment["prefix"])
    name = test_environment["job_global"]

    assert _value(samples, "docker_swarm_service_job_tasks_running", service_name=name, mode="global-job") == 0.0
    assert _value(samples, "docker_swarm_service_job_tasks_completed", service_name=name, mode="global-job") == 1.0
    assert _value(samples, "docker_swarm_service_job_tasks_target", service_name=name, mode="global-job") == 1.0
