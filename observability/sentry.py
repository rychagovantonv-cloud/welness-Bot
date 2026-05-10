import sentry_sdk
from loguru import logger

from config import settings


def setup_sentry() -> None:
    if not settings.sentry_dsn:
        logger.warning("SENTRY_DSN not set, error reporting disabled")
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.env,
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
    logger.info("sentry initialized", env=settings.env)
