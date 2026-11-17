"""
FastAPI application.   Run:  uvicorn app.main:app --reload     then open http://127.0.0.1:8000/docs
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pymongo.errors import PyMongoError

from .db import close_client, get_db
from .indexes import ensure_indexes
from .routers.crud import all_crud_routers
from .routers.extras import router as extras_router

log = logging.getLogger("api")

DESCRIPTION = """
CRUD API for the **Urban Tandoor** restaurant data stored in MongoDB.

* Every collection has the same endpoints: `POST`, `GET` (list), `GET /{id}`, `PUT /{id}`, `PATCH /{id}`, `DELETE /{id}`.
* `branch-stock` uses a composite id: `BRANCH_ID:INGREDIENT_ID`, e.g. `UTG-KHI-001:ING001`.
* `GET /search` finds dishes by meaning; `GET /availability/{branch_id}` gives live portions from stock.
* Changing menu items, deals, rules, allergens or ingredients automatically refreshes their embeddings in the background.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db = app.dependency_overrides.get(get_db, get_db)()      # honours test overrides
        ensure_indexes(db)                   # cheap and idempotent
    except Exception as exc:                 # keep the app up so /health can show what is wrong
        log.warning("Could not ensure indexes at startup: %s", exc)
    yield
    close_client()


app = FastAPI(title="Urban Tandoor API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan)

for r in all_crud_routers():
    app.include_router(r)
app.include_router(extras_router)


@app.exception_handler(PyMongoError)
async def mongo_error_handler(_: Request, exc: PyMongoError):
    return JSONResponse(status_code=503, content={"detail": f"Database error: {str(exc)[:200]}"})


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")
