import asyncio
import logging
import time
import uuid
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from telemetry.middleware import TelemetryMiddleware
from telemetry.db import get_summary

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [payment-service] %(message)s"
)
logger = logging.getLogger("payment-service")

app = FastAPI(
    title="ChaosPilot Payment Service",
    description="Payment microservice processing credit and debit transactions with injectable chaos modes.",
    version="1.0.4"
)
app.add_middleware(TelemetryMiddleware, service_name="payment-service")

# Fault State Definition
class FaultState:
    def __init__(self):
        self.active: bool = False
        self.mode: str = "timeout"  # 'timeout', '500', '503'
        self.delay_seconds: float = 5.0
        self.error_code: int = 504
        self.description: str = "Controlled payment-processing timeout fault"
        self.injected_at: Optional[float] = None

    def inject(self, mode: str = "timeout", delay_seconds: float = 5.0, error_code: int = 504, description: str = ""):
        self.active = True
        self.mode = mode
        self.delay_seconds = delay_seconds
        self.error_code = error_code
        self.description = description or "Controlled payment-processing timeout fault"
        self.injected_at = time.time()
        logger.warning(f"CHAOS INJECTED: mode={self.mode}, delay={self.delay_seconds}s, desc={self.description}")

    def reset(self):
        self.active = False
        self.mode = "timeout"
        self.delay_seconds = 5.0
        self.error_code = 504
        self.description = ""
        self.injected_at = None
        logger.info("CHAOS RESET: Service returned to nominal operating conditions.")

    def to_dict(self):
        return {
            "active": self.active,
            "mode": self.mode,
            "delay_seconds": self.delay_seconds,
            "error_code": self.error_code,
            "description": self.description,
            "injected_at": self.injected_at
        }

fault_state = FaultState()

# Request/Response Schemas
class PaymentRequest(BaseModel):
    order_id: str
    amount: float = Field(..., gt=0)
    currency: str = "USD"
    customer_id: str = "cust_anonymous"

class PaymentResponse(BaseModel):
    status: str
    transaction_id: str
    order_id: str
    amount: float
    currency: str
    timestamp: float

class FaultInjectRequest(BaseModel):
    mode: str = Field(default="timeout", description="'timeout', '500', or '503'")
    delay_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    error_code: int = Field(default=504)
    description: str = Field(default="Controlled payment-processing timeout fault introducing artificial delay exceeding upstream budget")

# Endpoints
@app.get("/health")
async def health_check():
    """Health check endpoint reflecting service status."""
    is_nominal = not fault_state.active
    return {
        "status": "healthy" if is_nominal else "degraded",
        "service": "payment-service",
        "version": "1.0.4",
        "timestamp": time.time(),
        "fault_active": fault_state.active,
        "fault_mode": fault_state.mode if fault_state.active else None
    }

@app.get("/metrics")
async def get_metrics(window: Optional[float] = None):
    """Exposes real calculated request telemetry for Payment Service."""
    return get_summary("payment-service", window_seconds=window)

@app.post("/payments/process", response_model=PaymentResponse)
async def process_payment(payment: PaymentRequest):
    """Processes payment for an order. Affected by fault injection."""
    logger.info(f"Incoming payment request: order_id={payment.order_id}, amount=${payment.amount} {payment.currency}")

    # Check for active fault injection
    if fault_state.active:
        if fault_state.mode == "timeout":
            logger.warning(
                f"[FAULT-ACTIVE] Introducing artificial delay of {fault_state.delay_seconds}s for order {payment.order_id}."
            )
            await asyncio.sleep(fault_state.delay_seconds)
            # If the client is still waiting after the sleep, raise timeout
            raise HTTPException(
                status_code=504,
                detail=f"Payment processing timed out: artificial delay of {fault_state.delay_seconds}s exceeded processing budget"
            )
        elif fault_state.mode == "500":
            logger.error(f"[FAULT-ACTIVE] Raising internal server error for order {payment.order_id}")
            raise HTTPException(
                status_code=500,
                detail="Internal Server Error: simulated payment backend processing error"
            )
        elif fault_state.mode == "503":
            logger.error(f"[FAULT-ACTIVE] Service Unavailable for order {payment.order_id}")
            raise HTTPException(
                status_code=503,
                detail="Service Unavailable: Payment backend upstream unreachable"
            )

    # Nominal payment processing (real simulated latency 20-40ms)
    await asyncio.sleep(0.025)
    txn_id = f"txn_{uuid.uuid4().hex[:12]}"
    logger.info(f"Payment successful: order_id={payment.order_id}, txn_id={txn_id}")
    return PaymentResponse(
        status="success",
        transaction_id=txn_id,
        order_id=payment.order_id,
        amount=payment.amount,
        currency=payment.currency,
        timestamp=time.time()
    )

# Fault Injection Control API
@app.post("/fault/inject")
async def inject_fault(config: FaultInjectRequest):
    """Controlled fault injection trigger for SRE simulation."""
    fault_state.inject(
        mode=config.mode,
        delay_seconds=config.delay_seconds,
        error_code=config.error_code,
        description=config.description
    )
    return {
        "status": "injected",
        "message": f"Fault successfully injected into payment-service ({config.mode})",
        "state": fault_state.to_dict()
    }

@app.post("/fault/reset")
async def reset_fault():
    """Recovers the service back to nominal operation."""
    fault_state.reset()
    return {
        "status": "nominal",
        "message": "payment-service fault cleared, returning to healthy state",
        "state": fault_state.to_dict()
    }

@app.get("/fault/status")
async def get_fault_status():
    """Returns the current fault state of payment-service."""
    return fault_state.to_dict()
