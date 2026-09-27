import logging
import time
import uuid
from typing import Dict, List, Optional
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.config import PAYMENT_SERVICE_URL, ORDER_TO_PAYMENT_TIMEOUT
from telemetry.middleware import TelemetryMiddleware
from telemetry.db import get_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [order-service] %(message)s"
)
logger = logging.getLogger("order-service")

app = FastAPI(
    title="ChaosPilot Order Service",
    description="Order processing microservice coordinating order state and payment delegation.",
    version="1.0.4"
)
app.add_middleware(TelemetryMiddleware, service_name="order-service")

# In-memory Order Store
orders_db: Dict[str, dict] = {}

class OrderItem(BaseModel):
    item_id: str
    name: str
    price: float
    quantity: int = 1

class OrderCreateRequest(BaseModel):
    customer_id: str = "cust_default"
    items: List[OrderItem] = Field(default_factory=list)
    total_amount: float = Field(..., gt=0)

class OrderResponse(BaseModel):
    order_id: str
    customer_id: str
    status: str
    payment_id: str
    total_amount: float
    created_at: float

@app.get("/health")
async def health_check():
    """Health check endpoint for Order Service."""
    return {
        "status": "healthy",
        "service": "order-service",
        "version": "1.0.4",
        "timestamp": time.time(),
        "total_orders_processed": len(orders_db)
    }

@app.get("/metrics")
async def get_metrics(window: Optional[float] = None):
    """Exposes real calculated request telemetry for Order Service."""
    return get_summary("order-service", window_seconds=window)

_client_pool: Optional[httpx.AsyncClient] = None

def get_client() -> httpx.AsyncClient:
    global _client_pool
    if _client_pool is None or _client_pool.is_closed:
        _client_pool = httpx.AsyncClient(
            timeout=ORDER_TO_PAYMENT_TIMEOUT,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
        )
    return _client_pool

@app.post("/orders/create", response_model=OrderResponse)
async def create_order(request: OrderCreateRequest):
    """Creates an order and executes payment transaction via Payment Service."""
    order_id = f"ord_{uuid.uuid4().hex[:10]}"
    logger.info(f"Initiating order {order_id} for customer {request.customer_id}, amount=${request.total_amount}")

    payment_payload = {
        "order_id": order_id,
        "amount": request.total_amount,
        "currency": "USD",
        "customer_id": request.customer_id
    }

    try:
        client = get_client()
        resp = await client.post(
            f"{PAYMENT_SERVICE_URL}/payments/process",
            json=payment_payload
        )

        if resp.status_code == 200:
            payment_data = resp.json()
            order_record = {
                "order_id": order_id,
                "customer_id": request.customer_id,
                "status": "confirmed",
                "payment_id": payment_data["transaction_id"],
                "total_amount": request.total_amount,
                "items": [item.dict() for item in request.items],
                "created_at": time.time()
            }
            orders_db[order_id] = order_record
            logger.info(f"Order {order_id} successfully confirmed with payment {payment_data['transaction_id']}")
            return OrderResponse(
                order_id=order_id,
                customer_id=request.customer_id,
                status="confirmed",
                payment_id=payment_data["transaction_id"],
                total_amount=request.total_amount,
                created_at=order_record["created_at"]
            )
        else:
            logger.error(f"Payment service rejected order {order_id}: HTTP {resp.status_code} - {resp.text}")
            raise HTTPException(
                status_code=502,
                detail=f"Payment service returned error {resp.status_code}: {resp.text}"
            )

    except httpx.TimeoutException:
        logger.error(f"Payment service timeout (> {ORDER_TO_PAYMENT_TIMEOUT}s) while processing order {order_id}")
        raise HTTPException(
            status_code=504,
            detail=f"Gateway Timeout: Payment service failed to respond within {ORDER_TO_PAYMENT_TIMEOUT}s limit."
        )
    except httpx.RequestError as exc:
        logger.error(f"Network failure connecting to payment service: {exc}")
        raise HTTPException(
            status_code=503,
            detail=f"Service Unavailable: Unable to reach payment service at {PAYMENT_SERVICE_URL}"
        )

@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    """Retrieves an existing order."""
    if order_id not in orders_db:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return orders_db[order_id]
