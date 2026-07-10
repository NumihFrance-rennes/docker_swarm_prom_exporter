# Docker Swarm Prometheus Exporter

Exporteur de métriques pour Docker Swarm qui expose le statut des mises à jour des services au format Prometheus.

## 🎯 Fonctionnalités

- **Statut des mises à jour** : Monitoring du statut des déploiements et rollbacks des services Docker Swarm
- **Nombre de replicas** : Suivi du nombre de replicas courants / désirés pour chaque service (`replicated` et `global`)
- **Suivi des jobs** : Nombre de tasks en cours / terminées / visées pour les services en mode job (`replicated-job` et `global-job`)

## 🖥️ Dashboard Grafana

Un dashboard prêt à l'emploi est fourni dans [`grafana/dashboard.json`](./grafana/dashboard.json) : un tableau listant chaque service avec son nombre de replicas courant/désiré, l'écart entre les deux (ligne surlignée en rouge en cas de sous-effectif), le statut de sa dernière mise à jour, et un filtre par nom de service en haut du dashboard.

**Import :**
1. Dans Grafana : **Dashboards** → **New** → **Import**
2. **Upload dashboard JSON file** → sélectionner `grafana/dashboard.json`
3. Choisir votre datasource Prometheus lorsque c'est demandé
4. **Import**

> **Note :** la datasource Prometheus est paramétrée (`${DS_PROMETHEUS}`) — Grafana vous demandera de la sélectionner au moment de l'import, aucune modification du JSON n'est nécessaire.

## Exemples d'alertes

```yaml
groups:
  - name: docker-swarm
    rules:
      # Alerte lorsqu'un service n'a pas le nombre de replicas désiré.
      # Le `for: 2m` évite de déclencher pendant un rolling update normal,
      # où l'écart est transitoire.
      - alert: DockerSwarmServiceReplicasMismatch
        expr: docker_swarm_service_replicas_current < docker_swarm_service_replicas_desired
        for: 2m
        labels:
          severity: critical
          notification: email
        annotations:
          summary: "{{ $labels.service_name }} n'a pas le nombre de replicas désiré"
          description: "{{ $labels.service_name }} ({{ $labels.mode }}) : {{ $value }} replicas actifs"

      # Alerte lorsque la dernière mise à jour d'un service est en échec ou en pause
      # (docker_swarm_service_update_status < 0.5 ne capture que 'failed'/'paused'/
      # 'rollback_paused' — une mise à jour en cours ('updating' = 0.5) ne déclenche pas).
      #
      # docker_swarm_service_update_status ne porte pas de label `mode` : si vous avez des
      # services "one-shot" (jobs d'init, de migration, ...) dont le statut de mise à jour
      # ne reflète pas un vrai déploiement en échec, excluez-les explicitement par nom via
      # le filtre `service_name!~"..."` ci-dessous (à adapter à vos propres services).
      - alert: DockerSwarmServiceUpdateFailed
        expr: docker_swarm_service_update_status{service_name!~"my-oneshot-job-1|my-oneshot-job-2"} < 0.5
        for: 1m
        labels:
          severity: critical
          notification: email
        annotations:
          summary: "Le déploiement du service {{ $labels.service_name }} est en échec"
          description: "Le déploiement du service {{ $labels.service_name }} est en échec (update_state={{ $labels.update_state }})"

      # Alerte lorsqu'un service job semble bloqué : il n'a pas encore atteint
      # son nombre de tasks cible et plus aucune task n'est en cours
      # d'exécution - un job sain finit par voir completed rejoindre target ;
      # ajustez le `for` à la durée normale de vos jobs les plus longs.
      - alert: DockerSwarmServiceJobStalled
        expr: docker_swarm_service_job_tasks_completed < docker_swarm_service_job_tasks_target and docker_swarm_service_job_tasks_running == 0
        for: 5m
        labels:
          severity: warning
          notification: email
        annotations:
          summary: "Le job {{ $labels.service_name }} semble bloqué"
          description: "{{ $labels.service_name }} ({{ $labels.mode }}) : {{ $value }} tasks terminées sur la cible, aucune en cours"

      # Alerte lorsqu'un job reste en cours d'exécution anormalement
      # longtemps (ex. process bloqué, boucle infinie) - un job est censé
      # se terminer, contrairement à un service replicated/global qui tourne
      # en continu. Ajustez le `for` à la durée normale de vos jobs les plus
      # longs pour éviter les faux positifs.
      - alert: DockerSwarmServiceJobRunningTooLong
        expr: docker_swarm_service_job_tasks_running > 0
        for: 1h
        labels:
          severity: warning
          notification: email
        annotations:
          summary: "Le job {{ $labels.service_name }} tourne depuis trop longtemps"
          description: "{{ $labels.service_name }} ({{ $labels.mode }}) : {{ $value }} tasks en cours depuis plus d'une heure"
```

## 📊 Métriques exposées

### docker_swarm_service_update_status

**Type :** Gauge  
**Description :** Statut de la dernière mise à jour du service  
**Labels :**
- `service_name` : Nom du service Docker Swarm
- `service_id` : ID court du service (12 premiers caractères)
- `update_state` : État de la mise à jour

**Valeurs :**
- `1.0` : Mise à jour terminée avec succès (`completed`, `rollback_completed`)
- `0.5` : Mise à jour en cours (`updating`, `rollback_started`)
- `0.0` : Mise à jour en pause ou échouée (`paused`, `failed`, `rollback_paused`)

**Exemple de sortie :**
```prometheus
# HELP docker_swarm_service_update_status Statut de la dernière mise à jour (1=completed, 0.5=updating, 0=failed)
# TYPE docker_swarm_service_update_status gauge
docker_swarm_service_update_status{service_name="web-app",service_id="abc123def456",update_state="completed"} 1.0
docker_swarm_service_update_status{service_name="api-service",service_id="def456ghi789",update_state="updating"} 0.5
docker_swarm_service_update_status{service_name="worker",service_id="ghi789jkl012",update_state="failed"} 0.0
```

### docker_swarm_service_replicas_current

**Type :** Gauge  
**Description :** Nombre de replicas actuellement en cours d'exécution (tasks en état `running`)  
**Labels :**
- `service_name` : Nom du service Docker Swarm
- `service_id` : ID court du service (12 premiers caractères)
- `mode` : Mode du service (`replicated` ou `global`)

**Exemple de sortie :**
```prometheus
# HELP docker_swarm_service_replicas_current Nombre de replicas actuellement en cours d'exécution (tasks en état running)
# TYPE docker_swarm_service_replicas_current gauge
docker_swarm_service_replicas_current{service_name="web-app",service_id="abc123def456",mode="replicated"} 3.0
docker_swarm_service_replicas_current{service_name="node-agent",service_id="def456ghi789",mode="global"} 5.0
```

### docker_swarm_service_replicas_desired

**Type :** Gauge  
**Description :** Nombre de replicas désirés  
**Labels :**
- `service_name` : Nom du service Docker Swarm
- `service_id` : ID court du service (12 premiers caractères)
- `mode` : Mode du service (`replicated` ou `global`)

**Calcul :**
- `replicated` : valeur exacte lue depuis `Spec.Mode.Replicated.Replicas`
- `global` : nombre de tasks non-terminales du service (une par nœud éligible), puisque Docker Swarm ne fixe pas de champ `Replicas` pour ce mode

**Exemple de sortie :**
```prometheus
# HELP docker_swarm_service_replicas_desired Nombre de replicas désirés (Spec.Replicas pour replicated, nombre de tasks non-terminales pour global)
# TYPE docker_swarm_service_replicas_desired gauge
docker_swarm_service_replicas_desired{service_name="web-app",service_id="abc123def456",mode="replicated"} 3.0
docker_swarm_service_replicas_desired{service_name="node-agent",service_id="def456ghi789",mode="global"} 5.0
```

> **Note :** Les services en mode `ReplicatedJob`/`GlobalJob` (tâches one-shot) ne sont pas couverts par ces deux métriques : une fois un job terminé avec succès, son nombre de tasks *en cours* retombe à 0 alors que le nombre *visé* reste positif, ce qui déclencherait indéfiniment l'alerte `DockerSwarmServiceReplicasMismatch` sur un job pourtant réussi. Ils sont couverts par les métriques dédiées `docker_swarm_service_job_tasks_*` ci-dessous.

### docker_swarm_service_job_tasks_running

**Type :** Gauge  
**Description :** Nombre de tasks actuellement en cours d'exécution pour un service job  
**Labels :**
- `service_name` : Nom du service Docker Swarm
- `service_id` : ID court du service (12 premiers caractères)
- `mode` : Mode du service (`replicated-job` ou `global-job`)

**Exemple de sortie :**
```prometheus
# HELP docker_swarm_service_job_tasks_running Nombre de tasks actuellement en cours d'exécution pour un service job
# TYPE docker_swarm_service_job_tasks_running gauge
docker_swarm_service_job_tasks_running{service_name="db-migration",service_id="abc123def456",mode="replicated-job"} 1.0
```

### docker_swarm_service_job_tasks_completed

**Type :** Gauge  
**Description :** Nombre de tasks terminées avec succès pour un service job  
**Labels :**
- `service_name` : Nom du service Docker Swarm
- `service_id` : ID court du service (12 premiers caractères)
- `mode` : Mode du service (`replicated-job` ou `global-job`)

**Exemple de sortie :**
```prometheus
# HELP docker_swarm_service_job_tasks_completed Nombre de tasks terminées avec succès pour un service job
# TYPE docker_swarm_service_job_tasks_completed gauge
docker_swarm_service_job_tasks_completed{service_name="db-migration",service_id="abc123def456",mode="replicated-job"} 2.0
```

### docker_swarm_service_job_tasks_target

**Type :** Gauge  
**Description :** Nombre total de tasks devant atteindre l'état `complete` pour que le job soit considéré terminé — une cible stable dans le temps, calculée par l'exporteur  
**Labels :**
- `service_name` : Nom du service Docker Swarm
- `service_id` : ID court du service (12 premiers caractères)
- `mode` : Mode du service (`replicated-job` ou `global-job`)

**Exemple de sortie :**
```prometheus
# HELP docker_swarm_service_job_tasks_target Nombre total de tasks devant atteindre l'état complete pour que le job soit considéré terminé
# TYPE docker_swarm_service_job_tasks_target gauge
docker_swarm_service_job_tasks_target{service_name="db-migration",service_id="abc123def456",mode="replicated-job"} 3.0
```

> **Prérequis :** la collecte des métriques `docker_swarm_service_job_tasks_*` nécessite Docker Engine ≥ 20.10 sur le nœud manager interrogé.

## 🚀 Construction et déploiement

### 1. Construire l'image

```bash
docker build -t docker-swarm-prometheus-exporter .
```

### 2. Lancement simple

```bash
docker run -d \
  --name swarm-exporter \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  docker-swarm-prometheus-exporter
```

### 3. Déploiement Swarm

```bash
docker service create \
  --name swarm-exporter \
  --mode global \
  --constraint 'node.role==manager' \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock,readonly \
  --publish 8080:8080 \
  docker-swarm-prometheus-exporter
```

### 4. Avec Docker Compose

```bash
docker stack deploy -c docker-compose.yml swarm-monitoring
```

## ⚙️ Configuration

Variables d'environnement disponibles :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `EXPORTER_PORT` | `8080` | Port d'écoute |
| `EXPORTER_INTERVAL` | `30` | Intervalle de collecte (secondes) |
| `LOG_LEVEL` | `INFO` | Niveau de log |

## 📈 Utilisation

Les métriques sont disponibles sur `http://localhost:8080/metrics`

Exemple de configuration Prometheus :
```yaml
scrape_configs:
  - job_name: 'docker-swarm'
    static_configs:
      - targets: ['localhost:8080']
```


## 🛡️ Sécurité

- L'image utilise un utilisateur non-root
- Le socket Docker est monté en lecture seule
- Déploiement recommandé sur les nœuds manager uniquement

## 📦 Images précompilées

Les images Docker sont automatiquement publiées sur GitHub Container Registry :

```bash
# Dernière version stable
docker pull ghcr.io/OWNER/REPO:latest

# Version spécifique
docker pull ghcr.io/OWNER/REPO:v1.0.0
```

> **Note :** Remplacez `OWNER/REPO` par le nom de votre dépôt GitHub

## 🚀 Publication automatique

### Workflow GitHub Actions

Le projet inclut un workflow GitHub Actions qui :

1. **Se déclenche automatiquement** lors d'un push de tag (format `v*.*.*`)
2. **Compile pour multiple architectures** (amd64, arm64) 
3. **Publie sur ghcr.io** avec authentification automatique
4. **Génère plusieurs tags** :
   - `v1.2.3` (tag exact)
   - `v1.2` (version majeure.mineure)
   - `v1` (version majeure)
   - `latest` (dernière version)

### Créer une release

```bash
# Créer et pousser un tag
git tag v1.0.0
git push origin v1.0.0

# L'image sera automatiquement disponible sur :
# ghcr.io/OWNER/REPO:v1.0.0
# ghcr.io/OWNER/REPO:v1.0
# ghcr.io/OWNER/REPO:v1
# ghcr.io/OWNER/REPO:latest
```