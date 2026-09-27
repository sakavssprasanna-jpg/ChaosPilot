import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Port Configurations
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8000"))
ORDER_PORT = int(os.getenv("ORDER_PORT", "8001"))
PAYMENT_PORT = int(os.getenv("PAYMENT_PORT", "8002"))

# Service URLs
HOST = os.getenv("SERVICE_HOST", "127.0.0.1")
GATEWAY_URL = f"http://{HOST}:{GATEWAY_PORT}"
ORDER_SERVICE_URL = f"http://{HOST}:{ORDER_PORT}"
PAYMENT_SERVICE_URL = f"http://{HOST}:{PAYMENT_PORT}"

# Timeouts (in seconds)
GATEWAY_TO_ORDER_TIMEOUT = float(os.getenv("GATEWAY_TIMEOUT", "3.0"))
ORDER_TO_PAYMENT_TIMEOUT = float(os.getenv("ORDER_TIMEOUT", "2.0"))

# Service Registry Mapping
SERVICE_MAP = {
    "gateway": {
        "module": "services.gateway",
        "port": GATEWAY_PORT,
        "url": GATEWAY_URL,
        "health_path": "/health"
    },
    "order-service": {
        "module": "services.order_service",
        "port": ORDER_PORT,
        "url": ORDER_SERVICE_URL,
        "health_path": "/health"
    },
    "payment-service": {
        "module": "services.payment_service",
        "port": PAYMENT_PORT,
        "url": PAYMENT_SERVICE_URL,
        "health_path": "/health"
    }
}
