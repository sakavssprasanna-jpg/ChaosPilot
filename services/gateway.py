import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.config import (
    ORDER_SERVICE_URL,
    PAYMENT_SERVICE_URL,
    GATEWAY_TO_ORDER_TIMEOUT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [gateway] %(message)s"
)
logger = logging.getLogger("gateway")

from telemetry.middleware import TelemetryMiddleware
from telemetry.db import get_summary

app = FastAPI(
    title="ChaosPilot API Gateway",
    description="Edge API Gateway routing incoming client traffic to Order and Payment microservices.",
    version="1.0.4"
)
app.add_middleware(TelemetryMiddleware, service_name="gateway")

class ItemModel(BaseModel):
    item_id: str
    name: str
    price: float
    quantity: int = 1

class CheckoutRequest(BaseModel):
    customer_id: str = "cust_web_user_1"
    items: List[ItemModel] = Field(default_factory=list)
    total_amount: float = Field(..., gt=0)

class CheckoutResponse(BaseModel):
    status: str
    order_id: str
    payment_id: str
    amount_charged: float
    customer_id: str
    timestamp: float

@app.get("/health")
async def health_check():
    """Edge gateway health check."""
    return {
        "status": "healthy",
        "service": "gateway",
        "version": "1.0.4",
        "timestamp": time.time()
    }

@app.get("/metrics")
async def get_metrics(window: Optional[float] = None):
    """Exposes real calculated request telemetry for Gateway."""
    return get_summary("gateway", window_seconds=window)

_client_pool: Optional[httpx.AsyncClient] = None

def get_client() -> httpx.AsyncClient:
    global _client_pool
    if _client_pool is None or _client_pool.is_closed:
        _client_pool = httpx.AsyncClient(
            timeout=GATEWAY_TO_ORDER_TIMEOUT,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
        )
    return _client_pool

@app.post("/api/checkout", response_model=CheckoutResponse)
async def checkout(request: CheckoutRequest):
    """Client checkout endpoint: calls Order Service, which calls Payment Service."""
    logger.info(f"Incoming checkout request for customer {request.customer_id}, total=${request.total_amount}")
    order_payload = {
        "customer_id": request.customer_id,
        "items": [item.dict() for item in request.items],
        "total_amount": request.total_amount
    }

    try:
        client = get_client()
        resp = await client.post(
            f"{ORDER_SERVICE_URL}/orders/create",
            json=order_payload
        )

        if resp.status_code == 200:
            order_data = resp.json()
            logger.info(f"Checkout completed successfully for order {order_data['order_id']}")
            return CheckoutResponse(
                status="success",
                order_id=order_data["order_id"],
                payment_id=order_data["payment_id"],
                amount_charged=order_data["total_amount"],
                customer_id=order_data["customer_id"],
                timestamp=time.time()
            )
        else:
            logger.error(f"Order Service responded with error {resp.status_code}: {resp.text}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Checkout failed: {resp.text}"
            )

    except httpx.TimeoutException:
        logger.error(f"Checkout timed out waiting for Order Service (> {GATEWAY_TO_ORDER_TIMEOUT}s)")
        raise HTTPException(
            status_code=504,
            detail=f"Gateway Timeout: Order service failed to respond within {GATEWAY_TO_ORDER_TIMEOUT}s"
        )
    except httpx.RequestError as exc:
        logger.error(f"Cannot connect to downstream Order Service: {exc}")
        raise HTTPException(
            status_code=503,
            detail=f"Service Unavailable: Cannot connect to Order Service at {ORDER_SERVICE_URL}"
        )

@app.get("/api/system/status")
async def system_status():
    """Aggregated health status of the entire microservices topology."""
    results: Dict[str, Any] = {
        "gateway": {"status": "healthy", "url": "local"},
        "order_service": {"status": "unknown"},
        "payment_service": {"status": "unknown"},
        "overall": "healthy"
    }

    async with httpx.AsyncClient(timeout=1.5) as client:
        # Check Order Service
        try:
            order_res = await client.get(f"{ORDER_SERVICE_URL}/health")
            results["order_service"] = order_res.json() if order_res.status_code == 200 else {"status": "unhealthy", "code": order_res.status_code}
        except Exception as e:
            results["order_service"] = {"status": "down", "error": str(e)}

        # Check Payment Service
        try:
            pay_res = await client.get(f"{PAYMENT_SERVICE_URL}/health")
            results["payment_service"] = pay_res.json() if pay_res.status_code == 200 else {"status": "unhealthy", "code": pay_res.status_code}
        except Exception as e:
            results["payment_service"] = {"status": "down", "error": str(e)}

    # Determine overall status
    is_down = results["order_service"].get("status") == "down" or results["payment_service"].get("status") == "down"
    is_degraded = results["payment_service"].get("status") == "degraded"

    if is_down:
        results["overall"] = "down"
    elif is_degraded:
        results["overall"] = "degraded"
    else:
        results["overall"] = "healthy"

    return results
