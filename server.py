"""
Webhook-server för intervals.icu → AI-träningsgenerator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Lyssnar på webhooks från intervals.icu och startar träningsgeneratorn
i bakgrunden när relevanta events inträffar.

Krav:
    pip install flask

Miljövariabler:
    WEBHOOK_SECRET   – Kopieras från intervals.icu webhook-inställningar
    PORT             – (valfritt, default 8080, sätts automatiskt av Render)

Kör lokalt:
    python webhook_server.py

På Render:
    Start command: gunicorn webhook_server:app
    (pip install gunicorn)
"""

import os
import sys
import subprocess
import threading
import logging
from datetime import datetime

from flask import Flask, request, jsonify

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Konfiguration ──────────────────────────────────────────────────────────────
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# Endast dessa event-typer triggar en ny plan.
# ACTIVITY_ANALYZED = passet är klart analyserat (TSS, zoner etc. är beräknade)
# WELLNESS_UPDATED  = ny wellness-data (HRV, sömn) har kommit in
RELEVANT_EVENTS = {"ACTIVITY_ANALYZED", "WELLNESS_UPDATED"}

# Skript att köra – relativt till denna fils katalog
GENERATOR_SCRIPT = os.path.join(os.path.dirname(__file__), "training_plan_generator.py")

# ── Lås – förhindrar parallella körningar ──────────────────────────────────────
_lock = threading.Lock()
_last_run: datetime | None = None
MIN_MINUTES_BETWEEN_RUNS = 5  # Spärr: kör inte oftare än var 5:e minut


# ── Flask-app ──────────────────────────────────────────────────────────────────
app = Flask(__name__)


def run_training_generator(trigger_event: str):
    """
    Kör training_plan_generator.py i en subprocess med --auto.
    Använder ett lås så att bara en körning kan ske åt gången.
    """
    global _last_run

    # Försök hämta låset utan att vänta
    if not _lock.acquire(blocking=False):
        log.info("⏭️   Generator körs redan – skippar detta webhook-anrop")
        return

    try:
        # Tidsspärr: förhindrar storm av körningar vid burst-webhooks
        if _last_run is not None:
            elapsed = (datetime.now() - _last_run).total_seconds() / 60
            if elapsed < MIN_MINUTES_BETWEEN_RUNS:
                log.info(
                    f"⏭️   Senaste körning för {elapsed:.1f} min sedan "
                    f"(min {MIN_MINUTES_BETWEEN_RUNS} min) – skippar"
                )
                return

        log.info(f"🚀  Startar AI-träningsgeneratorn (trigger: {trigger_event})...")
        _last_run = datetime.now()

        result = subprocess.run(
            [sys.executable, GENERATOR_SCRIPT, "--auto"],
            capture_output=True,
            text=True,
            timeout=300,  # Max 5 minuter – annars timeout
        )

        if result.returncode == 0:
            log.info("✅  Generatorn kördes framgångsrikt!")
            for line in result.stdout.splitlines():
                if line.strip():
                    log.info(f"   [AI] {line}")
        else:
            log.error("❌  Generatorn avslutades med fel:")
            for line in result.stderr.splitlines():
                if line.strip():
                    log.error(f"   [ERR] {line}")

    except subprocess.TimeoutExpired:
        log.error("❌  Generatorn tog för lång tid (>5 min) – avbruten")
    except Exception as e:
        log.error(f"❌  Oväntat fel vid start av generator: {e}")
    finally:
        _lock.release()


@app.route("/webhook", methods=["POST"])
def intervals_webhook():
    """
    Tar emot webhooks från intervals.icu.
    Svarar alltid 200 direkt, kör generatorn i bakgrunden om relevant.
    """
    data = request.get_json(silent=True)
    if not data:
        log.warning("⚠️   Tom eller ogiltig JSON-payload")
        return jsonify({"error": "Invalid JSON"}), 400

    # TEMPORÄR DEBUG – ta bort när du hittat din secret
    log.info(f"📥  Full payload: {data}")

    # 1. Verifiera webhook-secret
    if WEBHOOK_SECRET and data.get("secret") != WEBHOOK_SECRET:
        log.warning("⚠️   Felaktig webhook-secret – avvisar anrop")
        return jsonify({"error": "Unauthorized"}), 401

    # 2. Filtrera event-typer
    events      = data.get("events", [])
    event_types = [e.get("type") for e in events if e.get("type")]

    if not event_types:
        log.info("📥  Webhook utan event-typer – ignorerar")
        return jsonify({"status": "ignored", "reason": "no event types"}), 200

    relevant = [t for t in event_types if t in RELEVANT_EVENTS]
    if not relevant:
        log.info(f"⏭️   Irrelevanta event-typer {event_types} – ignorerar")
        return jsonify({"status": "ignored", "reason": f"event types {event_types} not relevant"}), 200

    trigger = relevant[0]
    log.info(f"📥  Relevant webhook: {trigger} – startar generator i bakgrunden")

    # 3. Starta generator i bakgrundstråd och svara direkt
    thread = threading.Thread(
        target=run_training_generator,
        args=(trigger,),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "status":  "accepted",
        "trigger": trigger,
        "message": "Träningsplan genereras i bakgrunden.",
    }), 200


@app.route("/", methods=["GET"])
def health_check():
    """Health check – används av Render för att bekräfta att servern lever."""
    status = {
        "status":   "online",
        "last_run": _last_run.isoformat() if _last_run else "aldrig",
        "locked":   _lock.locked(),
    }
    return jsonify(status), 200


@app.route("/trigger", methods=["POST"])
def manual_trigger():
    """
    Manuell trigger för testning – kräver samma secret som webhooks.
    curl -X POST https://din-server/trigger -H 'X-Secret: din-secret'
    """
    if WEBHOOK_SECRET and request.headers.get("X-Secret") != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    log.info("🔧  Manuell trigger mottagen")
    thread = threading.Thread(
        target=run_training_generator,
        args=("MANUAL_TRIGGER",),
        daemon=True,
    )
    thread.start()
    return jsonify({"status": "accepted", "message": "Generator startad manuellt."}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    log.info(f"🌐  Startar webhook-server på port {port}")
    if not WEBHOOK_SECRET:
        log.warning("⚠️   WEBHOOK_SECRET är inte satt – alla anrop accepteras!")
    app.run(host="0.0.0.0", port=port)