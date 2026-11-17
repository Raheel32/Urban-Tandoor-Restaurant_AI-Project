"""MongoDB connection. One shared client for the whole app (PyMongo is thread-safe)."""
from pymongo import MongoClient
from pymongo.database import Database

from . import config

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=8000, appname="urban-tandoor")
    return _client


def get_db() -> Database:
    """FastAPI dependency: `db: Database = Depends(get_db)`. Tests replace it with an in-memory database."""
    return get_client()[config.MONGODB_DB]


def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
