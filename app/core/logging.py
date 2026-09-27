# AI service core: logging.
# Config, logging, HTTP, or cache used by WhatsApp orchestration.
import logging
import sys
import json
from datetime import datetime
from typing import Any, Dict


# Jsonformatter.
class JSONFormatter(logging.Formatter):
    # Format.
    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            log_data["request_id"] = getattr(record, "request_id")
        if hasattr(record, "entity_id"):
            log_data["entity_id"] = getattr(record, "entity_id")
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


# Setup logger.
def setup_logger(name: str = "ai_service") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)

    return logger


logger = setup_logger()
