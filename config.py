"""Central configuration. Edit paths/models here."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Claude API ---
# The LLM your AGENT calls at runtime (separate from Claude Code, which builds this repo).
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Cheap model for schema-linking / validation; strong model for generation / correction.
# Split models = part of the efficiency story. Update strings if newer models ship.
MODEL_CHEAP = os.getenv("MODEL_CHEAP", "claude-haiku-4-5-20251001")
MODEL_STRONG = os.getenv("MODEL_STRONG", "claude-sonnet-5")

TEMPERATURE = 0.0        # deterministic for reproducibility
MAX_TOKENS = 1024

# --- BIRD data layout ---
# After downloading BIRD dev, point these at the unzipped files.
# Expected structure:
#   data/dev/dev.json
#   data/dev/dev_databases/{db_id}/{db_id}.sqlite
DATA_ROOT = os.getenv("DATA_ROOT", "data")

SPLITS = {
    "mini": os.path.join(DATA_ROOT, "mini_dev", "mini_dev.json"),
    "dev": os.path.join(DATA_ROOT, "dev", "dev.json"),
}
DB_ROOT = {
    "mini": os.path.join(DATA_ROOT, "mini_dev", "dev_databases"),
    "dev": os.path.join(DATA_ROOT, "dev", "dev_databases"),
}

# --- Agent behavior ---
MAX_CORRECTION_ATTEMPTS = 3   # self-correction loop cap (controls cost)
SQL_TIMEOUT_SECONDS = 30      # kill runaway queries during eval
