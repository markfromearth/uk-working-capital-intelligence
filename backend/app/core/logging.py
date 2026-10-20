import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

correlation_id_ctx: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)

def get_correlation_id() -> str:
    cid = correlation_id_ctx.get()
    if not cid:
        cid = str(uuid.uuid4())
        correlation_id_ctx.set(cid)
    return cid

def set_correlation_id(cid: str) -> None:
    correlation_id_ctx.set(cid)

class StructuredJsonFormatter(logging.Formatter):
    """Formats log records into structured JSON including correlation ID and timestamp."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_ctx.get() or "none",
        }
        
        # Include extra attributes if provided
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            log_obj.update(record.extra)
            
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
            
        return json.dumps(log_obj)

def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("working_capital")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
        
    return logger

logger = setup_logging()
