"""Price watch tool for MXP → HAN round-trip with date flexibility and email alert.

Target dates:
- Partenza: 23–25 dic 2026
- Ritorno:  8–10 gen 2027

Alert: invia una email UNA SOLA VOLTA quando il prezzo scende sotto la soglia
configurata (750 EUR).
"""

from __future__ import annotations

import json
import logging
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants – DATE E SOGLIA AGGIORNATE
# ---------------------------------------------------------------------------

ORIGIN = "MXP"
DESTINATION = "HAN"

# Finestra Partenza (23, 24, 25 Dicembre 2026)
OUTBOUND_DATES: list[str] = ["2026-12-23", "2026-12-24", "2026-12-25"]

# Finestra Ritorno (8, 9, 10 Gennaio 2027)
RETURN_DATES: list[str] = ["2027-01-08", "2027-01-09", "2027-01-10"]

# Nuova soglia impostata a 750 EUR
ALERT_THRESHOLD_EUR = 750.0

_WATCH_DIR = Path.home() / ".fli" / "price_watch"
_WATCH_FILE = _WATCH_DIR / "mxp_han_flex.json"
_EMAIL_CONFIG_FILE = Path("C:/Users/stefa/Desktop/Piton/fli-main/fli/mcp/email_config.json")


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _load_history() -> dict[str, Any]:
    """Load existing price history from disk, or return an empty structure."""
    if _WATCH_FILE.exists():
        try:
            return json.loads(_WATCH_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read price-watch file %s: %s", _WATCH_FILE, exc)
    return {
        "route": f"{ORIGIN}→{DESTINATION}",
        "snapshots": [],
        "alert_sent": False,
    }


def _save_history(history: dict[str, Any]) -> None:
    """Persist price history to disk (creates parent directories if needed)."""
    _WATCH_DIR.mkdir(parents=True, exist_ok=True)
    _WATCH_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _today_utc() -> str:
    """Return today's date in UTC as YYYY-MM-DD."""
    return datetime.now(timezone.utc).date().isoformat()


def _already_snapped_today(snapshots: list[dict]) -> bool:
    """Return True if the most recent snapshot was taken today (UTC)."""
    if not snapshots:
        return False
    return snapshots[-1].get("ts", "")[:10] ==
