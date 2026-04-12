"""Application entrypoint."""

import uvicorn

from src.logging_config import setup_logging
from src.models.config import Settings

settings = Settings()
setup_logging(settings.log_level)

if __name__ == "__main__":
    uvicorn.run(
        "src.api.app:app",
        host="0.0.0.0",
        port=8100,
        reload=True,
        log_level=settings.log_level.lower(),
    )
