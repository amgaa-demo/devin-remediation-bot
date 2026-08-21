"""Central configuration. Everything comes from environment variables."""
import os

DEVIN_API_KEY = os.environ["DEVIN_API_KEY"]          # cog_... service user key
DEVIN_ORG_ID = os.environ["DEVIN_ORG_ID"]
DEVIN_API_BASE = os.environ.get("DEVIN_API_BASE", "https://api.devin.ai/v3")

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]            # needs Issues:write on the fork
GITHUB_REPO = os.environ["GITHUB_REPO"]              # e.g. "my-org/superset"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")  # GitHub webhook HMAC secret

TRIGGER_LABEL = os.environ.get("TRIGGER_LABEL", "devin-remediate")
HUMAN_LABEL = os.environ.get("HUMAN_LABEL", "needs-human")

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
DB_PATH = os.environ.get("DB_PATH", "/data/state.db")
