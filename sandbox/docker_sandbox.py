import shutil
from typing import Any, Dict, List
from sandbox.base import TargetEnvironment

class DockerTargetEnvironment(TargetEnvironment):
    """Docker-based target environment adapter.
    
    Acts as an interface stub for containerized microservice architectures.
    Gracefully raises descriptive errors when Docker is unavailable.
    """

    def __init__(self, compose_file: str = "services/docker-compose.yml"):
        self.compose_file = compose_file
        self.docker_available = shutil.which("docker") is not None

    def _require_docker(self):
        if not self.docker_available:
            raise EnvironmentError(
                "Docker is not installed or not available in system PATH. "
                "Use ProcessTargetEnvironment for native process-based SRE simulation."
            )

    def start_all(self) -> Dict[str, bool]:
        self._require_docker()
        raise NotImplementedError("Docker container orchestration requires active Docker daemon.")

    def stop_all(self) -> Dict[str, bool]:
        self._require_docker()
        raise NotImplementedError("Docker container orchestration requires active Docker daemon.")

    def start_service(self, service_name: str) -> bool:
        self._require_docker()
        raise NotImplementedError("Docker container orchestration requires active Docker daemon.")

    def stop_service(self, service_name: str) -> bool:
        self._require_docker()
        raise NotImplementedError("Docker container orchestration requires active Docker daemon.")

    def restart_service(self, service_name: str) -> bool:
        self._require_docker()
        raise NotImplementedError("Docker container orchestration requires active Docker daemon.")

    def check_health(self, service_name: str) -> Dict[str, Any]:
        self._require_docker()
        raise NotImplementedError("Docker container orchestration requires active Docker daemon.")

    def check_all_health(self) -> Dict[str, Dict[str, Any]]:
        self._require_docker()
        raise NotImplementedError("Docker container orchestration requires active Docker daemon.")

    def get_logs(self, service_name: str, lines: int = 50) -> List[str]:
        self._require_docker()
        raise NotImplementedError("Docker container orchestration requires active Docker daemon.")

    def is_running(self, service_name: str) -> bool:
        self._require_docker()
        return False
