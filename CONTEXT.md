# Docker Swarm Prometheus Exporter

Exporteur qui traduit l'état des services Docker Swarm (déploiements, replicas) en métriques Prometheus.

## Language

**Service**:
Une définition de charge de travail Docker Swarm (`docker service`), identifiée par `service_id`/`service_name`. Peut être en mode `replicated` ou `global`.
_Avoid_: application, workload

**Task**:
Une instance d'exécution d'un service, programmée par Swarm sur un nœud. Passe par plusieurs états (`new`, `pending`, ..., `running`, puis un état terminal comme `complete`/`failed`/`shutdown`).
_Avoid_: instance, container (une task encapsule un conteneur mais n'y est pas réductible)

**Replica**:
Une task d'un service dans l'état `running`.
_Avoid_: instance, replica count (seul, sans préciser current/desired)

**Current replicas**:
Le nombre de tasks d'un service en état `running`, à l'instant de la collecte.

**Desired replicas**:
Le nombre de replicas qu'un service devrait avoir. Pour un service `replicated`, c'est `Spec.Mode.Replicated.Replicas` (valeur exacte et stable). Pour un service `global`, il n'existe pas de champ équivalent — c'est déduit en comptant les tasks non-terminales du service (une par nœud éligible).

**Job service** (hors périmètre):
Un service en mode `ReplicatedJob`/`GlobalJob` (tâche one-shot qui se termine après exécution). Volontairement ignoré par les métriques de replicas : ses tasks finissent normalement en `complete`, donc la notion de "replicas en cours" ne s'applique pas.
_Avoid_: global job, replicated job (utiliser "job service" comme terme générique)
