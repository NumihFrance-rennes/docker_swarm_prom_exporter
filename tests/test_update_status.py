from conftest import metrics_for

VALID_MODE_LABELS = {"replicated", "global", "replicated-job", "global-job", "unknown"}


def test_update_status_carries_mode_label(test_environment, exporter):
    samples = metrics_for(exporter, test_environment["prefix"])
    update_samples = [s for s in samples if s.name == "docker_swarm_service_update_status"]

    assert update_samples, "docker_swarm_service_update_status absente pour les services de test"
    for sample in update_samples:
        assert sample.labels.get("mode") in VALID_MODE_LABELS


def test_update_status_present_for_forced_update(test_environment, exporter):
    samples = metrics_for(exporter, test_environment["prefix"])
    name = test_environment["replicated"]

    matching = [
        s for s in samples
        if s.name == "docker_swarm_service_update_status" and s.labels.get("service_name") == name
    ]
    assert matching, f"docker_swarm_service_update_status absente pour {name}"
    assert matching[0].labels.get("mode") == "replicated"
