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
# Constants
# ---------------------------------------------------------------------------

ORIGIN = "MXP"
DESTINATION = "HAN"
OUTBOUND_DATES: list[str] = ["2026-12-23", "2026-12-24", "2026-12-25"]
RETURN_DATES: list[str] = ["2027-01-08", "2027-01-09", "2027-01-10"]
ALERT_THRESHOLD_EUR = 750.0

_WATCH_DIR = Path.home() / ".fli" / "price_watch"
_WATCH_FILE = _WATCH_DIR / "mxp_han_flex.json"
_EMAIL_CONFIG_FILE = Path("C:/Users/stefa/Desktop/Piton/fli-main/fli/mcp/email_config.json")


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _load_history() -> dict[str, Any]:
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
    _WATCH_DIR.mkdir(parents=True, exist_ok=True)
    # Rimuoviamo eventuali chiavi complesse annidate prima del salvataggio
    clean_history = history.copy()
    if "snapshots" in clean_history:
        clean_snaps = []
        for s in clean_history["snapshots"]:
            s_copy = s.copy()
            s_copy.pop("all_combinations", None)
            clean_snaps.append(s_copy)
        clean_history["snapshots"] = clean_snaps

    _WATCH_FILE.write_text(
        json.dumps(clean_history, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _already_snapped_today(snapshots: list[dict]) -> bool:
    if not snapshots:
        return False
    return snapshots[-1].get("ts", "")[:10] == _today_utc()


# ---------------------------------------------------------------------------
# Email configuration + sending
# ---------------------------------------------------------------------------


def _load_email_config() -> dict[str, str] | None:
    if not _EMAIL_CONFIG_FILE.exists():
        logger.warning("Email config not found at %s", _EMAIL_CONFIG_FILE)
        return None
    try:
        cfg = json.loads(_EMAIL_CONFIG_FILE.read_text(encoding="utf-8"))
        required = {"smtp_host", "smtp_port", "username", "password", "to_address"}
        if not required.issubset(cfg.keys()):
            return None
        return cfg
    except (json.JSONDecodeError, OSError):
        return None


def _send_alert_email(snapshot: dict[str, Any], threshold: float) -> bool:
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
    body = (
        f"Buone notizie! Il prezzo del tuo volo sorvegliato è sceso sotto la soglia.\n\n"
        f"  Rotta        : Milano Malpensa (MXP) → Hanoi (HAN)\n"
        f"  Prezzo       : {price:.2f} {currency} (soglia: {threshold:.0f} {currency})\n"
        f"  Partenza     : {outbound}\n"
        f"  Ritorno      : {ret}\n"
        f"  Compagnia    : {airline}\n"
        f"  Scali        : {stops}\n"
        f"  Durata tot.  : {duration_h}h {duration_m}m\n\n"
        f"Controlla subito su Google Flights: https://www.google.com/flights"
    )

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
        return True
    except Exception as exc:
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
    price = snapshot.get("price")
    if price is None or not isinstance(price, int | float):
        return {"alert_checked": False, "reason": "prezzo non disponibile"}

    alert_sent_before = history.get("alert_sent", False)
    result = {
        "soglia_eur": threshold,
        "prezzo_attuale": price,
        "sotto_soglia": price < threshold,
        "alert_sent_before": alert_sent_before,
    }

    if price >= threshold:
        if alert_sent_before:
            history["alert_sent"] = False
            result["azione"] = "prezzo risalito sopra soglia – flag alert reimpostato"
        else:
            result["azione"] = "prezzo sopra soglia – nessuna azione"
        return result

    if alert_sent_before:
        result["azione"] = "prezzo sotto soglia ma alert già inviato – skip"
        return result

    if _send_alert_email(snapshot, threshold):
        history["alert_sent"] = True
        result["azione"] = "✅ email di alert inviata"
    else:
        result["azione"] = "⚠️ prezzo sotto soglia ma invio email fallito"
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
        "price": float(price) if price is not None else None,
        "currency": str(currency_code),
        "stops": int(stops_count),
        "airline": str(airline),
        "duration_min": int(duration_min),
    }


# ---------------------------------------------------------------------------
# Multi-date clean sequential search
# ---------------------------------------------------------------------------


def _fetch_best_price(currency: str = "EUR") -> dict[str, Any] | None:
    from fli.core import parse_cabin_class, parse_max_stops, resolve_airport

    origin_ap = resolve_airport(ORIGIN)
    dest_ap = resolve_airport(DESTINATION)
    seat_type = parse_cabin_class("ECONOMY")
    stops = parse_max_stops("ANY")

    candidates = []

    for out_d in OUTBOUND_DATES:
        for ret_d in RETURN_DATES:
            try:
                res = _search_one_pair(origin_ap, dest_ap, seat_type, stops, out_d, ret_d, currency)
                if res is not None:
                    candidates.append(res)
            except Exception:
                continue

    if not candidates:
        return None

    # Estraiamo il migliore e creiamo un dizionario pulito senza riferimenti complessi
    raw_best = min(candidates, key=lambda c: c["price"])
    best = {
        "outbound_date": raw_best["outbound_date"],
        "return_date": raw_best["return_date"],
        "price": raw_best["price"],
        "currency": raw_best["currency"],
        "stops": raw_best["stops"],
        "airline": raw_best["airline"],
        "duration_min": raw_best["duration_min"],
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    }
    return best


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_price_watch(
    force_refresh: bool = False,
    currency: str = "EUR",
    alert_threshold: float = ALERT_THRESHOLD_EUR,
) -> dict[str, Any]:
    history = _load_history()
    snapshots = history.setdefault("snapshots", [])

    fetched_new = False
    error_msg = None
    alert_result = {}

    if force_refresh or not _already_snapped_today(snapshots):
        try:
            snapshot = _fetch_best_price(currency=currency)
            if snapshot:
                if snapshots and snapshots[-1].get("ts", "")[:10] == _today_utc():
                    snapshots[-1] = snapshot
                else:
                    snapshots.append(snapshot)

                alert_result = _check_and_send_alert(snapshot, history, alert_threshold)
                _save_history(history)
                fetched_new = True
            else:
                error_msg = "Nessun volo trovato nelle finestre di date indicate."
        except Exception as exc:
            error_msg = f"Errore durante l'elaborazione: {exc}"
    else:
        if snapshots:
            latest_snap = snapshots[-1]
            alert_result = {
                "soglia_eur": alert_threshold,
                "prezzo_attuale": latest_snap.get("price"),
                "azione": "Già controllato oggi.",
            }

    latest = snapshots[-1] if snapshots else None
    
    # Restituiamo un dizionario finale ultra-pulito per evitare crash in json.dumps
    return {
        "success": latest is not None,
        "fetched_new_snapshot": fetched_new,
        "error": error_msg,
        "alert": {k: v for k, v in alert_result.items() if isinstance(v, (str, int, float, bool, None.__class__))},
        "latest_snapshot": {k: v for k, v in latest.items() if k != "all_combinations"} if latest else None
    }
