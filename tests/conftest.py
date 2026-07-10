import os
import subprocess
import sys
import time

import docker
import pytest
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exporter import DockerSwarmExporter  # noqa: E402

STACK_NAME = "exporter-metrics-test"
STACK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stack.yml")
REPLICATED_NAME = f"{STACK_NAME}_replicated"
GLOBAL_NAME = f"{STACK_NAME}_global"
JOB_REPLICATED_NAME = f"{STACK_NAME}_job-r"
JOB_GLOBAL_NAME = f"{STACK_NAME}_job-g"


def wait_until(predicate, timeout=60, interval=1):
    """Poll `predicate` jusqu'à ce qu'elle soit vraie ou que `timeout` (secondes) expire."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _run(cmd):
    subprocess.run(cmd, check=False, capture_output=True)


def _cleanup(client):
    _run(["docker", "stack", "rm", STACK_NAME])
    _run(["docker", "service", "rm", JOB_REPLICATED_NAME])
    _run(["docker", "service", "rm", JOB_GLOBAL_NAME])
    wait_until(
        lambda: len(client.services.list(filters={"name": STACK_NAME})) == 0,
        timeout=30,
    )


def _service_status(client, name):
    services = client.services.list(filters={"name": name}, status=True)
    matching = [s for s in services if s.name == name]
    if not matching:
        return {}
    return matching[0].attrs.get("ServiceStatus") or {}


def _update_state(client, name):
    services = client.services.list(filters={"name": name})
    matching = [s for s in services if s.name == name]
    if not matching:
        return None
    return matching[0].attrs.get("UpdateStatus", {}).get("State")


@pytest.fixture(scope="session")
def docker_client():
    client = docker.from_env()
    swarm_active = False
    try:
        swarm_active = bool(client.swarm.attrs)
    except Exception as e:
        pytest.fail(f"Docker Swarm inaccessible: {e}")
    if not swarm_active:
        pytest.fail(
            "Docker Swarm n'est pas actif sur cette machine (lancer `docker swarm init`)"
        )
    return client


@pytest.fixture(scope="session")
def test_environment(docker_client):
    # Nettoyage défensif : un run précédent interrompu (CI tuée, Ctrl+C local)
    # peut avoir laissé des résidus avant que le teardown normal ne s'exécute.
    _cleanup(docker_client)

    subprocess.run(
        ["docker", "stack", "deploy", "-c", STACK_FILE, STACK_NAME],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "docker", "service", "create", "--name", JOB_REPLICATED_NAME,
            "--mode", "replicated-job", "--replicas", "3",
            "alpine", "echo", "hello",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "docker", "service", "create", "--name", JOB_GLOBAL_NAME,
            "--mode", "global-job",
            "alpine", "echo", "hello",
        ],
        check=True,
        capture_output=True,
    )

    ready = all([
        wait_until(lambda: _service_status(docker_client, REPLICATED_NAME).get("RunningTasks", 0) >= 2),
        wait_until(lambda: _service_status(docker_client, GLOBAL_NAME).get("RunningTasks", 0) >= 1),
        wait_until(lambda: _service_status(docker_client, JOB_REPLICATED_NAME).get("CompletedTasks", 0) >= 3),
        wait_until(lambda: _service_status(docker_client, JOB_GLOBAL_NAME).get("CompletedTasks", 0) >= 1),
    ])
    if not ready:
        _cleanup(docker_client)
        pytest.fail("La stack/les services de test n'ont pas atteint l'état attendu à temps")

    # docker_swarm_service_update_status n'est posée que si le service a un
    # historique de mise à jour : un service jamais mis à jour n'a pas de champ
    # UpdateStatus du tout. On force une mise à jour sur `replicated` pour que
    # les tests puissent observer cette métrique.
    subprocess.run(
        ["docker", "service", "update", "--force", REPLICATED_NAME],
        check=True,
        capture_output=True,
    )
    wait_until(lambda: _update_state(docker_client, REPLICATED_NAME) == "completed", timeout=30)

    yield {
        "prefix": STACK_NAME,
        "replicated": REPLICATED_NAME,
        "global": GLOBAL_NAME,
        "job_replicated": JOB_REPLICATED_NAME,
        "job_global": JOB_GLOBAL_NAME,
    }

    _cleanup(docker_client)


@pytest.fixture
def exporter():
    return DockerSwarmExporter(port=0)


def metrics_for(exporter_instance, prefix):
    """Collecte les métriques puis ne retourne que les séries dont
    `service_name` commence par `prefix`, pour isoler des autres services
    réels du swarm (ex. une stack `infra_*` tournant en local)."""
    exporter_instance.collect_metrics()
    text = generate_latest(exporter_instance.registry).decode("utf-8")
    samples = []
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.labels.get("service_name", "").startswith(prefix):
                samples.append(sample)
    return samples
