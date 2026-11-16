"""MongoDB client creation and the readiness probe."""

import logging

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from notifications.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

type MongoDocument = dict[str, object]

_SERVER_SELECTION_TIMEOUT_MS = 3_000


def create_mongo_client(settings: Settings) -> AsyncMongoClient[MongoDocument]:
    """Create the async MongoDB client.

    The client connects lazily, so creating it never blocks. All timestamps are read back as
    timezone-aware UTC datetimes (Principle IV).
    """
    return AsyncMongoClient(
        settings.mongodb_uri,
        document_class=dict,
        tz_aware=True,
        serverSelectionTimeoutMS=_SERVER_SELECTION_TIMEOUT_MS,
        appname="ecommerce-notifications",
    )


class MongoDatabaseProbe:
    """Readiness probe that pings MongoDB."""

    def __init__(self, client: AsyncMongoClient[MongoDocument]) -> None:
        """Wrap the shared client."""
        self._client = client

    async def is_reachable(self) -> bool:
        """Return ``True`` when MongoDB answers ``ping``; log and return ``False`` otherwise."""
        try:
            await self._client.admin.command("ping")
        except PyMongoError as exc:
            logger.warning("MongoDB ping failed: %s", exc)
            return False
        return True
