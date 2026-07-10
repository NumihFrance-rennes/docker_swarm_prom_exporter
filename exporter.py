#!/usr/bin/env python3
"""
Docker Swarm Prometheus Exporter

Ce script expose les métriques des services Docker Swarm au format Prometheus.
Il collecte les informations sur le statut des services, leur dernière mise à
jour, leur nombre de replicas (courant / désiré) pour les services
replicated/global, et l'avancement des tasks (running/completed/cible) pour
les services job.
"""

import time
import logging
import docker
from prometheus_client import Gauge, start_http_server
from prometheus_client.core import CollectorRegistry

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# États de task considérés comme terminaux (la task ne tourne plus et ne
# tournera plus). Utilisé pour déduire le nombre de replicas désirés des
# services `global`, qui n'ont pas de champ `Replicas` explicite.
TERMINAL_TASK_STATES = {'complete', 'failed', 'shutdown', 'rejected', 'orphaned', 'remove'}

# Modes de service "job" (tâches one-shot type `docker service create --mode
# replicated-job`). Contrairement à Replicated/Global, leurs tasks finissent
# normalement en `complete` : la notion de "replicas en cours" ne s'y
# applique pas, donc ils ont leurs propres métriques dédiées (voir
# docker_swarm_service_job_tasks_* dans _setup_metrics / collect_metrics).
JOB_SERVICE_MODES = {'ReplicatedJob', 'GlobalJob'}

# Labels `mode` exposés pour les services job, au format `docker service
# create --mode ...` plutôt qu'un `.lower()` mécanique de la clé API, pour
# rester reconnaissable par les utilisateurs de la CLI Docker.
JOB_MODE_LABELS = {
    'ReplicatedJob': 'replicated-job',
    'GlobalJob': 'global-job',
}

class DockerSwarmExporter:
    """Exporteur de métriques Docker Swarm pour Prometheus"""
    
    def __init__(self, port: int = 8080, interval: int = 30):
        """
        Initialise l'exporteur
        
        Args:
            port: Port d'écoute pour les métriques
            interval: Intervalle de mise à jour en secondes
        """
        self.port = port
        self.interval = interval
        self.registry = CollectorRegistry()
        
        # Initialisation du client Docker
        try:
            self.client = docker.from_env()
            # Test de connexion au daemon Docker Swarm
            self.client.swarm.attrs
            logger.info("Connexion réussie au daemon Docker Swarm")
        except docker.errors.APIError as e:
            logger.error(f"Erreur lors de la connexion à Docker Swarm: {e}")
            raise
        except Exception as e:
            logger.error(f"Erreur inattendue: {e}")
            raise
        
        # Définition des métriques Prometheus
        self._setup_metrics()
    
    def _setup_metrics(self):
        """Configure les métriques Prometheus"""
        
        # Métriques pour les mises à jour
        self.service_update_status = Gauge(
            'docker_swarm_service_update_status',
            'Statut de la dernière mise à jour (1=completed, 0.5=updating, 0=failed)',
            ['service_name', 'service_id', 'update_state'],
            registry=self.registry
        )

        # Métriques pour le nombre de replicas
        self.service_replicas_current = Gauge(
            'docker_swarm_service_replicas_current',
            'Nombre de replicas actuellement en cours d\'exécution (tasks en état running)',
            ['service_name', 'service_id', 'mode'],
            registry=self.registry
        )

        self.service_replicas_desired = Gauge(
            'docker_swarm_service_replicas_desired',
            'Nombre de replicas désirés (Spec.Replicas pour replicated, nombre de tasks non-terminales pour global)',
            ['service_name', 'service_id', 'mode'],
            registry=self.registry
        )

        # Métriques pour les services en mode job (ReplicatedJob/GlobalJob)
        self.service_job_tasks_running = Gauge(
            'docker_swarm_service_job_tasks_running',
            "Nombre de tasks actuellement en cours d'exécution pour un service job (ServiceStatus.RunningTasks)",
            ['service_name', 'service_id', 'mode'],
            registry=self.registry
        )

        self.service_job_tasks_completed = Gauge(
            'docker_swarm_service_job_tasks_completed',
            'Nombre de tasks terminées avec succès pour un service job (ServiceStatus.CompletedTasks)',
            ['service_name', 'service_id', 'mode'],
            registry=self.registry
        )

        self.service_job_tasks_target = Gauge(
            'docker_swarm_service_job_tasks_target',
            "Nombre total de tasks devant atteindre l'état complete pour que le job soit considéré terminé "
            "(cible stable, à ne pas confondre avec le champ Docker ServiceStatus.DesiredTasks qui compte "
            "le travail restant et retombe à 0 une fois le job fini)",
            ['service_name', 'service_id', 'mode'],
            registry=self.registry
        )

    def _get_update_status_value(self, update_status: str) -> float:
        """
        Convertit le statut de mise à jour en valeur numérique
        
        Args:
            update_status: Statut de mise à jour ('completed', 'updating', 'paused', 'failed', etc.)
            
        Returns:
            1.0 pour completed
            0.5 pour updating
            0.0 pour paused, failed ou autre
        """
        status_map = {
            'completed': 1.0,
            'updating': 0.5,
            'paused': 0.0,
            'failed': 0.0,
            'rollback_completed': 1.0,
            'rollback_paused': 0.0,
            'rollback_started': 0.5
        }
        return status_map.get(update_status.lower(), 0.0)

    def _group_tasks_by_service(self) -> dict:
        """
        Récupère toutes les tasks du swarm en un seul appel API et les
        regroupe par service_id.

        Un seul appel `client.api.tasks()` est utilisé ici plutôt qu'un
        appel `service.tasks()` par service, pour éviter un pattern N+1
        (1 appel + 1 par service) qui pèserait sur le daemon manager quand
        le nombre de services grandit.

        Returns:
            dict: service_id -> liste de tasks (dictionnaires bruts de l'API Docker)
        """
        tasks_by_service = {}
        for task in self.client.api.tasks():
            tasks_by_service.setdefault(task.get('ServiceID'), []).append(task)
        return tasks_by_service

    def _get_service_mode(self, service) -> str | None:
        """
        Détermine le mode d'un service ('Replicated', 'Global',
        'ReplicatedJob', 'GlobalJob') à partir de son spec.

        Returns:
            str: le nom du mode, ou None s'il n'est pas reconnu
        """
        mode_spec = service.attrs.get('Spec', {}).get('Mode', {})
        for mode in ('Replicated', 'Global', 'ReplicatedJob', 'GlobalJob'):
            if mode in mode_spec:
                return mode
        return None

    def _get_current_replicas(self, tasks: list) -> int:
        """Nombre de tasks en état 'running', quel que soit le mode du service."""
        return sum(
            1 for task in tasks
            if task.get('Status', {}).get('State', '').lower() == 'running'
        )

    def _get_desired_replicas(self, service, mode: str, tasks: list) -> int:
        """
        Nombre de replicas désirés.

        Pour un service 'replicated', c'est directement Spec.Replicas (valeur
        exacte, sans le bruit transitoire des tasks en cours d'arrêt pendant
        un scaling).

        Pour un service 'global', Swarm ne fixe aucun champ 'Replicas' : le
        nombre désiré est déduit en comptant les tasks non-terminales (une
        par nœud éligible), ce qui correspond à ce qu'affiche
        `docker service ps` sans dupliquer la logique de matching des
        contraintes de placement du scheduler Swarm.
        """
        if mode == 'Replicated':
            return service.attrs.get('Spec', {}).get('Mode', {}).get('Replicated', {}).get('Replicas', 0)

        return sum(
            1 for task in tasks
            if task.get('Status', {}).get('State', '').lower() not in TERMINAL_TASK_STATES
        )

    def _get_job_target_tasks(self, service, mode: str, tasks: list) -> int:
        """
        Intention : représenter la cible de complétion d'un job service,
        c'est-à-dire le nombre total de tasks qui doivent atteindre l'état
        'complete' pour que le job soit considéré terminé. Cette cible est
        stable dans le temps (elle ne varie pas selon l'avancement du job),
        contrairement au champ Docker ServiceStatus.DesiredTasks (voir plus
        bas) qui mesure autre chose.

        Pour 'ReplicatedJob', c'est Spec.Mode.ReplicatedJob.TotalCompletions ;
        si ce champ est absent, l'API Swarm indique qu'il vaut par défaut
        MaxConcurrent (lui-même par défaut 1) - on reproduit ce repli ici
        plutôt que de supposer que le champ est toujours présent en pratique.
        Note : ServiceStatus.DesiredTasks n'est PAS une alternative valable
        ici - le code de swarmkit (manager/controlapi/service.go) l'assigne
        directement à Spec.ReplicatedJob.MaxConcurrent, pas à
        TotalCompletions ; ce serait donc la limite de concurrence, pas la
        cible de complétion, si jamais MaxConcurrent < TotalCompletions.

        Pour 'GlobalJob', Spec ne porte aucun champ équivalent (une task par
        nœud éligible). ServiceStatus.DesiredTasks semblait un candidat
        naturel (c'est ce que fait _get_desired_replicas pour 'global'), mais
        pour un GlobalJob ce champ ne compte (toujours d'après le code de
        swarmkit) que les tasks dont l'état désiré 'Completed' n'est pas
        encore atteint - une fois le job fini, il n'y a plus rien à compter
        et il retombe à 0, ce qui ne peut donc pas servir de cible stable. On
        compte à la place le nombre de nœuds distincts (NodeID) parmi les
        tasks du service : Swarm assigne exactement une task par nœud
        éligible pour un GlobalJob, et ce compte reste correct que le job
        soit en cours ou terminé.
        """
        if mode == 'ReplicatedJob':
            replicated_job_spec = service.attrs.get('Spec', {}).get('Mode', {}).get('ReplicatedJob', {})
            total_completions = replicated_job_spec.get('TotalCompletions')
            if total_completions is not None:
                return total_completions
            return replicated_job_spec.get('MaxConcurrent', 1)

        return len({task['NodeID'] for task in tasks if task.get('NodeID')})

    def collect_metrics(self):
        """Collecte les métriques des services Docker Swarm"""
        try:
            logger.info("Collecte des métriques Docker Swarm...")

            # Réinitialisation des gauges avant collecte
            self.service_update_status.clear()
            self.service_replicas_current.clear()
            self.service_replicas_desired.clear()
            self.service_job_tasks_running.clear()
            self.service_job_tasks_completed.clear()
            self.service_job_tasks_target.clear()

            # Collecte des informations sur les services. status=True demande
            # au daemon de calculer ServiceStatus (RunningTasks/DesiredTasks/
            # CompletedTasks), nécessaire pour les métriques des services job.
            # Requiert une API négociée >= 1.41 (Docker Engine >= 20.10).
            services = self.client.services.list(status=True)
            logger.info(f"Trouvé {len(services)} services")

            # Un seul appel API pour toutes les tasks du swarm (voir _group_tasks_by_service)
            tasks_by_service = self._group_tasks_by_service()

            for service in services:
                service_name = service.name
                service_id = service.id[:12]  # Prendre seulement les 12 premiers caractères

                try:
                    # Statut de mise à jour
                    update_status = service.attrs.get('UpdateStatus', {})
                    if update_status:
                        state = update_status.get('State', 'unknown')
                        update_value = self._get_update_status_value(state)

                        self.service_update_status.labels(
                            service_name=service_name,
                            service_id=service_id,
                            update_state=state
                        ).set(update_value)

                    # Nombre de replicas (current / desired)
                    mode = self._get_service_mode(service)
                    if mode in ('Replicated', 'Global'):
                        tasks = tasks_by_service.get(service.id, [])
                        mode_label = mode.lower()

                        self.service_replicas_current.labels(
                            service_name=service_name,
                            service_id=service_id,
                            mode=mode_label
                        ).set(self._get_current_replicas(tasks))

                        self.service_replicas_desired.labels(
                            service_name=service_name,
                            service_id=service_id,
                            mode=mode_label
                        ).set(self._get_desired_replicas(service, mode, tasks))
                    elif mode in JOB_SERVICE_MODES:
                        mode_label = JOB_MODE_LABELS[mode]
                        service_status = service.attrs.get('ServiceStatus') or {}
                        tasks = tasks_by_service.get(service.id, [])

                        self.service_job_tasks_running.labels(
                            service_name=service_name,
                            service_id=service_id,
                            mode=mode_label
                        ).set(service_status.get('RunningTasks', 0))

                        self.service_job_tasks_completed.labels(
                            service_name=service_name,
                            service_id=service_id,
                            mode=mode_label
                        ).set(service_status.get('CompletedTasks', 0))

                        self.service_job_tasks_target.labels(
                            service_name=service_name,
                            service_id=service_id,
                            mode=mode_label
                        ).set(self._get_job_target_tasks(service, mode, tasks))
                    else:
                        logger.warning(
                            f"Service {service_name} ignoré : mode de service non reconnu ({mode})"
                        )

                except Exception as e:
                    logger.error(f"Erreur lors du traitement du service {service_name}: {e}")
                    continue
            
            logger.info("Collecte des métriques terminée avec succès")
            
        except Exception as e:
            logger.error(f"Erreur lors de la collecte des métriques: {e}")
    
    def run(self):
        """Lance l'exporteur de métriques"""
        logger.info(f"Démarrage de l'exporteur sur le port {self.port}")
        
        # Démarrage du serveur HTTP Prometheus
        start_http_server(self.port, registry=self.registry)
        logger.info(f"Serveur de métriques démarré sur http://0.0.0.0:{self.port}/metrics")
        
        # Boucle principale de collecte
        while True:
            try:
                self.collect_metrics()
                logger.debug(f"Attente de {self.interval} secondes avant la prochaine collecte")
                time.sleep(self.interval)
            except KeyboardInterrupt:
                logger.info("Arrêt demandé par l'utilisateur")
                break
            except Exception as e:
                logger.error(f"Erreur dans la boucle principale: {e}")
                logger.info(f"Nouvelle tentative dans {self.interval} secondes")
                time.sleep(self.interval)

def main():
    """Point d'entrée principal"""
    import os
    
    # Configuration depuis les variables d'environnement
    port = int(os.getenv('EXPORTER_PORT', '8080'))
    interval = int(os.getenv('EXPORTER_INTERVAL', '30'))
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    
    # Configuration du niveau de log
    numeric_level = getattr(logging, log_level, logging.INFO)
    logging.getLogger().setLevel(numeric_level)
    
    logger.info(f"Configuration: port={port}, interval={interval}s, log_level={log_level}")
    
    # Création et lancement de l'exporteur
    exporter = DockerSwarmExporter(port=port, interval=interval)
    exporter.run()

if __name__ == '__main__':
    main()