"""Price watch tool for MXP → HAN round-trip with date flexibility and email alert.

Target dates:
- Partenza ideale:  24 dicembre 2026  →  finestra: 22–26 dic 2026 (±2 giorni)
- Ritorno ideale:   11 gennaio 2027   →  finestra:  9–11 gen 2027 (solo anticipo, -2 giorni)

Alert: invia una email UNA SOLA VOLTA quando il prezzo scende sotto la soglia
configurata (default 700 EUR). L'alert non viene ripetuto nei giorni successivi
finché il prezzo non risale sopra soglia e poi torna a scendere.

Configurazione email
--------------------
Prima di usare il tool, crea il file::

    C:\\Users\\<TuoNome>\\.fli\\email_config.json

con questo contenuto (esempio per Gmail)::

    {
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "username": "tua.email@gmail.com",
      "password": "xxxx xxxx xxxx xxxx",
      "to_address": "tua.email@gmail.com"
    }

Per Gmail la "password" deve essere una App Password (non la tua password normale).
Vedi: https://myaccount.google.com/apppasswords
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
# Constants – target dates and tolerance window
# ---------------------------------------------------------------------------

ORIGIN = "MXP"
DESTINATION = "HAN"

_OUTBOUND_TARGET = date(2026, 12, 24)
_OUTBOUND_TOLERANCE = 2           # ± days  →  22–26 Dec 2026
_RETURN_TARGET = date(2027, 1, 11)
_RETURN_TOLERANCE_BEFORE = 2      # only earlier  →  9–11 Jan 2027
_RETURN_TOLERANCE_AFTER = 0

# All candidate outbound dates: 22–26 Dec 2026  (5 dates)
OUTBOUND_DATES: list[str] = [
    (_OUTBOUND_TARGET + timedelta(days=d)).isoformat()
    for d in range(-_OUTBOUND_TOLERANCE, _OUTBOUND_TOLERANCE + 1)
]

# All candidate return dates: 9–11 Jan 2027  (3 dates)
RETURN_DATES: list[str] = [
    (_RETURN_TARGET + timedelta(days=d)).isoformat()
    for d in range(-_RETURN_TOLERANCE_BEFORE, _RETURN_TOLERANCE_AFTER + 1)
]

ALERT_THRESHOLD_EUR = 700.0   # send email when price drops below this

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
        # Tracks whether the alert has already been sent for the current
        # "below-threshold episode" so we don't spam on every daily check.
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
    return snapshots[-1].get("ts", "")[:10] == _today_utc()


# ---------------------------------------------------------------------------
# Email configuration + sending
# ---------------------------------------------------------------------------


def _load_email_config() -> dict[str, str] | None:
    """Load SMTP credentials from ~/.fli/email_config.json.

    Returns the config dict, or None if the file is missing or invalid.
    """
    if not _EMAIL_CONFIG_FILE.exists():
        logger.warning(
            "Email config not found at %s – alert email will be skipped.",
            _EMAIL_CONFIG_FILE,
        )
        return None
    try:
        cfg = json.loads(_EMAIL_CONFIG_FILE.read_text(encoding="utf-8"))
        required = {"smtp_host", "smtp_port", "username", "password", "to_address"}
        missing = required - cfg.keys()
        if missing:
            logger.warning("Email config missing keys: %s", missing)
            return None
        return cfg
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read email config: %s", exc)
        return None


def _send_alert_email(snapshot: dict[str, Any], threshold: float) -> bool:
    """Send a price-alert email using the credentials in email_config.json.

    Args:
        snapshot: The current best-price snapshot dict.
        threshold: The price threshold that was crossed (EUR).

    Returns:
        True if the email was sent successfully, False otherwise.
    """
    cfg = _load_email_config()
    if cfg is None:
        return False

    price = snapshot.get("price", "?")
    currency = snapshot.get("currency", "EUR")
    outbound = snapshot.get("outbound_date", "?")
    ret = snapshot.get("return_date", "?")
    airline = snapshot.get("airline", "?")
    stops = snapshot.get("stops", "?")
    duration_h = int(snapshot.get("duration_min", 0) // 60)
    duration_m = int(snapshot.get("duration_min", 0) % 60)

    subject = f"✈️ Alert volo MXP→HAN: prezzo sceso a {price:.0f} {currency}!"

    # Build a readable plain-text body
    lines = [
        "Buone notizie! Il prezzo del tuo volo sorvegliato è sceso sotto la soglia.",
        "",
        f"  Rotta        : Milano Malpensa (MXP) → Hanoi (HAN) andata e ritorno",
        f"  Prezzo       : {price:.2f} {currency}  (soglia: {threshold:.0f} {currency})",
        f"  Partenza     : {outbound}",
        f"  Ritorno      : {ret}",
        f"  Compagnia    : {airline}",
        f"  Scali        : {stops}",
        f"  Durata tot.  : {duration_h}h {duration_m}m",
        "",
        "Controlla subito su Google Flights per acquistare il biglietto:",
        "https://www.google.com/flights",
        "",
        "— Il tuo monitoraggio fli automatico",
    ]
    body = "\n".join(lines)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["username"]
    msg["To"] = cfg["to_address"]
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["username"], cfg["to_address"], msg.as_string())
        logger.info("Alert email sent to %s", cfg["to_address"])
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send alert email: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Alert logic
# ---------------------------------------------------------------------------


def _check_and_send_alert(
    snapshot: dict[str, Any],
    history: dict[str, Any],
    threshold: float = ALERT_THRESHOLD_EUR,
) -> dict[str, Any]:
    """Evaluate whether an alert email should be sent and send it if needed.

    Rules:
    - Send the email only if price < threshold AND alert_sent is False.
    - Mark alert_sent = True after sending so it won't repeat next day.
    - Reset alert_sent to False when the price rises back above threshold,
      so a future drop below threshold triggers a new alert.

    Returns a dict describing what happened (included in the tool output).
    """
    price = snapshot.get("price")
    if price is None or not isinstance(price, int | float):
        return {"alert_checked": False, "reason": "prezzo non disponibile"}

    alert_sent_before: bool = history.get("alert_sent", False)
    result: dict[str, Any] = {
        "soglia_eur": threshold,
        "prezzo_attuale": price,
        "sotto_soglia": price < threshold,
        "alert_sent_before": alert_sent_before,
    }

    if price >= threshold:
        # Price is above threshold: reset flag so next drop triggers alert
        if alert_sent_before:
            history["alert_sent"] = False
            result["azione"] = "prezzo risalito sopra soglia – flag alert reimpostato"
        else:
            result["azione"] = "prezzo sopra soglia – nessuna azione"
        return result

    # Price is below threshold
    if alert_sent_before:
        result["azione"] = "prezzo sotto soglia ma alert già inviato in precedenza – skip"
        return result

    # First time below threshold: send email
    sent = _send_alert_email(snapshot, threshold)
    if sent:
        history["alert_sent"] = True
        result["azione"] = "✅ email di alert inviata"
        result["email_inviata"] = True
    else:
        result["azione"] = (
            "⚠️ prezzo sotto soglia ma invio email fallito "
            "(controlla ~/.fli/email_config.json)"
        )
        result["email_inviata"] = False

    return result


# ---------------------------------------------------------------------------
# Single date-pair search
# ---------------------------------------------------------------------------


def _search_one_pair(
    origin_ap,
    dest_ap,
    seat_type,
    stops,
    outbound_date: str,
    return_date: str,
    currency: str,
) -> dict[str, Any] | None:
    """Search Google Flights for one specific outbound+return date pair."""
    from fli.core import build_flight_segments
    from fli.models import FlightSearchFilters, PassengerInfo
    from fli.search import SearchFlights

    segments, trip_type = build_flight_segments(
        origin=origin_ap,
        destination=dest_ap,
        departure_date=outbound_date,
        return_date=return_date,
    )
    filters = FlightSearchFilters(
        trip_type=trip_type,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=segments,
        stops=stops,
        seat_type=seat_type,
        show_all_results=True,
    )

    client = SearchFlights()
    results = client.search(filters, currency=currency)
    if not results:
        return None

    best = results[0]
    if isinstance(best, tuple):
        outbound_res, return_res = best[0], best[1]
        price = outbound_res.price
        currency_code = outbound_res.currency or currency
        stops_count = outbound_res.stops + return_res.stops
        duration_min = outbound_res.duration + return_res.duration
        primary = outbound_res.primary_airline
        legs = outbound_res.legs
    else:
        price = best.price
        currency_code = best.currency or currency
        stops_count = best.stops
        duration_min = best.duration
        primary = best.primary_airline
        legs = best.legs

    airline = (
        primary.name.lstrip("_")
        if primary
        else (legs[0].airline.name.lstrip("_") if legs else "?")
    )

    return {
        "outbound_date": outbound_date,
        "return_date": return_date,
        "price": price,
        "currency": currency_code,
        "stops": stops_count,
        "airline": airline,
        "duration_min": duration_min,
    }


# ---------------------------------------------------------------------------
# Multi-date parallel search  (5 outbound × 3 return = 15 searches)
# ---------------------------------------------------------------------------


def _fetch_best_price(currency: str = "EUR") -> dict[str, Any] | None:
    """Search all 15 date combinations in parallel and return the cheapest."""
    from fli.core import parse_cabin_class, parse_max_stops, resolve_airport
    from fli.search._concurrency import parallel_map

    origin_ap = resolve_airport(ORIGIN)
    dest_ap = resolve_airport(DESTINATION)
    seat_type = parse_cabin_class("ECONOMY")
    stops = parse_max_stops("ANY")

    pairs = [
        (out_d, ret_d)
        for out_d in OUTBOUND_DATES
        for ret_d in RETURN_DATES
    ]

    def search_pair(pair: tuple[str, str]) -> dict[str, Any] | None:
        out_d, ret_d = pair
        try:
            return _search_one_pair(
                origin_ap, dest_ap, seat_type, stops, out_d, ret_d, currency
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Pair %s / %s failed: %s", out_d, ret_d, exc)
            return None

    raw = parallel_map(search_pair, pairs)
    candidates = [r for r in raw if r is not None]
    if not candidates:
        return None

    best = min(candidates, key=lambda c: c["price"])
    best["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    best["all_combinations"] = sorted(candidates, key=lambda c: c["price"])
    return best


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def _compute_stats(snapshots: list[dict]) -> dict[str, Any]:
    """Derive trend stats and best-ever record from the snapshot list."""
    if not snapshots:
        return {}

    prices = [s["price"] for s in snapshots if isinstance(s.get("price"), int | float)]
    if not prices:
        return {}

    best_snap = min(snapshots, key=lambda s: s.get("price", float("inf")))
    latest = snapshots[-1]

    if len(prices) >= 2:
        delta = prices[-1] - prices[-2]
        if delta > 5:
            trend = f"↑ +{delta:.0f} rispetto a ieri"
        elif delta < -5:
            trend = f"↓ {delta:.0f} rispetto a ieri"
        else:
            trend = "→ stabile rispetto a ieri"
    else:
        trend = "primo rilevamento"

    return {
        "prezzo_attuale": latest.get("price"),
        "valuta": latest.get("currency"),
        "data_partenza_migliore_oggi": latest.get("outbound_date"),
        "data_ritorno_migliore_oggi": latest.get("return_date"),
        "compagnia_attuale": latest.get("airline"),
        "scali_attuali": latest.get("stops"),
        "durata_min_attuale": latest.get("duration_min"),
        "finestra_partenza": f"{OUTBOUND_DATES[0]} → {OUTBOUND_DATES[-1]}",
        "finestra_ritorno": f"{RETURN_DATES[0]} → {RETURN_DATES[-1]}",
        "soglia_alert_eur": ALERT_THRESHOLD_EUR,
        "prezzo_minimo_storico": best_snap.get("price"),
        "data_rilevamento_minimo": best_snap.get("ts", "")[:10],
        "partenza_al_minimo_storico": best_snap.get("outbound_date"),
        "ritorno_al_minimo_storico": best_snap.get("return_date"),
        "compagnia_al_minimo_storico": best_snap.get("airline"),
        "trend": trend,
        "numero_rilevamenti": len(snapshots),
        "primo_rilevamento": snapshots[0].get("ts", "")[:10],
        "ultimo_rilevamento": latest.get("ts", "")[:10],
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_price_watch(
    force_refresh: bool = False,
    currency: str = "EUR",
    alert_threshold: float = ALERT_THRESHOLD_EUR,
) -> dict[str, Any]:
    """Search all 15 date combinations, save the daily snapshot, send alert if needed.

    Args:
        force_refresh: When True, performs a fresh search even if a snapshot
            already exists for today (UTC).
        currency: ISO 4217 currency code for prices (default ``EUR``).
        alert_threshold: Send an email alert when price drops below this value
            (default 700 EUR). The alert is sent only once per price episode.

    Returns:
        A result dict with success status, stats, alert info, latest snapshot,
        full history, and the path to the on-disk JSON file.
    """
    history = _load_history()
    snapshots: list[dict] = history.setdefault("snapshots", [])

    fetched_new = False
    error_msg: str | None = None
    alert_result: dict[str, Any] = {}

    if force_refresh or not _already_snapped_today(snapshots):
        try:
            snapshot = _fetch_best_price(currency=currency)
            if snapshot:
                if snapshots and snapshots[-1].get("ts", "")[:10] == _today_utc():
                    snapshots[-1] = snapshot
                else:
                    snapshots.append(snapshot)

                # Check alert BEFORE saving so alert_sent flag is persisted
                alert_result = _check_and_send_alert(snapshot, history, alert_threshold)
                _save_history(history)
                fetched_new = True
            else:
                error_msg = (
                    "Nessun volo trovato per MXP→HAN nelle finestre di date "
                    f"(partenza {OUTBOUND_DATES[0]}–{OUTBOUND_DATES[-1]}, "
                    f"ritorno {RETURN_DATES[0]}–{RETURN_DATES[-1]})."
                )
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Errore durante la ricerca: {exc.__class__.__name__}: {exc}"
            logger.exception("Price watch search failed")
    else:
        # Already snapped today: still report alert status for transparency
        if snapshots:
            latest_snap = snapshots[-1]
            price = latest_snap.get("price")
            alert_result = {
                "soglia_eur": alert_threshold,
                "prezzo_attuale": price,
                "sotto_soglia": price is not None and price < alert_threshold,
                "alert_sent_before": history.get("alert_sent", False),
                "azione": "già controllato oggi – nessuna nuova ricerca eseguita",
            }

    stats = _compute_stats(snapshots)
    latest = snapshots[-1] if snapshots else None

    if error_msg and not snapshots:
        return {
            "success": False,
            "route": f"{ORIGIN}→{DESTINATION} (andata e ritorno flessibile)",
            "outbound_window": f"{OUTBOUND_DATES[0]} → {OUTBOUND_DATES[-1]}",
            "return_window": f"{RETURN_DATES[0]} → {RETURN_DATES[-1]}",
            "error": error_msg,
            "history_path": str(_WATCH_FILE),
        }

    return {
        "success": True,
        "route": f"{ORIGIN}→{DESTINATION} (andata e ritorno flessibile)",
        "outbound_window": f"{OUTBOUND_DATES[0]} → {OUTBOUND_DATES[-1]}",
        "return_window": f"{RETURN_DATES[0]} → {RETURN_DATES[-1]}",
        "fetched_new_snapshot": fetched_new,
        "already_checked_today": not fetched_new and not force_refresh,
        "warning": error_msg,
        "alert": alert_result,
        "stats": stats,
        "latest_snapshot": latest,
        "all_snapshots": snapshots,
        "history_path": str(_WATCH_FILE),
    }
