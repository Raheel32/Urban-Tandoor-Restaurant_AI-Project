"""Settings, read once from environment variables / the .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
JSON_DIR = ROOT / "data" / "json"

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "urban_tandoor")

EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").lower()      # gemini | fake
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "20"))
EMBED_SLEEP_SECONDS = float(os.getenv("EMBED_SLEEP_SECONDS", "1.0"))

VECTOR_SEARCH_MODE = os.getenv("VECTOR_SEARCH_MODE", "auto").lower()         # auto | atlas | python
VECTOR_INDEX_NAME = "documents_vector_index"
AUTO_REINDEX = os.getenv("AUTO_REINDEX", "true").lower() in ("1", "true", "yes")
