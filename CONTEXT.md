# Docker Swarm Prometheus Exporter

Exporteur qui traduit l'état des services Docker Swarm (déploiements, replicas) en métriques Prometheus.

## Language

**Service**:
Une définition de charge de travail Docker Swarm (`docker service`), identifiée par `service_id`/`service_name`. Peut être en mode `replicated`, `global`, `replicated-job` ou `global-job` (voir Job service).
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

**Job service**:
Un service en mode `ReplicatedJob`/`GlobalJob` (tâche one-shot qui se termine après exécution, par opposition à un service `replicated`/`global` qui tourne en continu). Ses tasks finissent normalement en `complete` : la notion de "replicas en cours" (current/desired replicas) ne s'y applique pas — ces services ont leurs propres métriques dédiées (running/completed/target tasks).
_Avoid_: global job, replicated job (utiliser "job service" comme terme générique)

**Running tasks (job)**:
Le nombre de tasks d'un job service en état `running` à l'instant de la collecte.

**Completed tasks (job)**:
Le nombre de tasks d'un job service ayant terminé avec succès.

**Target tasks (job)**:
Le nombre total de tasks qu'un job service doit voir passer à `complete` pour être considéré terminé — une cible stable dans le temps, calculée par l'exporteur.
_Avoid_: desired tasks (job) — nom délibérément évité pour ce terme.
