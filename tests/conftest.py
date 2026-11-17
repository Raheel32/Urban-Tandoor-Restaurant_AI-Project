import os

# Must be set BEFORE the app is imported (settings are read at import time).
os.environ["EMBEDDING_PROVIDER"] = "fake"
os.environ["VECTOR_SEARCH_MODE"] = "python"
os.environ["AUTO_REINDEX"] = "true"
os.environ["EMBED_SLEEP_SECONDS"] = "0"          # no rate-limit pauses in tests

from pathlib import Path

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.ingest import ingest, load_workbooks
from app.main import app

DATA = Path(__file__).resolve().parents[1] / "data" / "json"


@pytest.fixture(scope="session")
def workbooks():
    return load_workbooks(DATA)


@pytest.fixture()
def db(workbooks):
    """A fresh in-memory MongoDB, loaded with the real JSON data, for every test."""
    database = mongomock.MongoClient().urban_test
    ingest(database, *workbooks)
    return database


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
