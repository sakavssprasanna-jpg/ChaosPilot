from abc import ABC, abstractmethod
from typing import Any, Dict, List

class TargetEnvironment(ABC):
    """Abstract interface defining the safe lifecycle management for target services."""

    @abstractmethod
    def start_all(self) -> Dict[str, bool]:
        """Starts all target services in the environment."""
        pass

    @abstractmethod
    def stop_all(self) -> Dict[str, bool]:
        """Stops all running target services."""
        pass

    @abstractmethod
    def start_service(self, service_name: str) -> bool:
        """Starts a specific named service."""
        pass

    @abstractmethod
    def stop_service(self, service_name: str) -> bool:
        """Stops a specific named service."""
        pass

    @abstractmethod
    def restart_service(self, service_name: str) -> bool:
        """Safely restarts a specific named service and waits for health."""
        pass

    @abstractmethod
    def check_health(self, service_name: str) -> Dict[str, Any]:
        """Checks the health of a specific named service."""
        pass

    @abstractmethod
    def check_all_health(self) -> Dict[str, Dict[str, Any]]:
        """Checks health across all registered services."""
        pass

    @abstractmethod
    def get_logs(self, service_name: str, lines: int = 50) -> List[str]:
        """Collects the most recent log lines for a specific service."""
        pass

    @abstractmethod
    def is_running(self, service_name: str) -> bool:
        """Checks whether a service process/container is actively running."""
        pass
