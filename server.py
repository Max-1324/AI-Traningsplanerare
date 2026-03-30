"""
Liten webbserver som lyssnar på Webhooks från Intervals.icu.
När en uppdatering sker, startar den träningsgeneratorn i bakgrunden.
"""

import os
import subprocess
import threading
import logging
from flask import Flask, request, jsonify

# Sätt upp loggning så att vi kan se vad som händer i Renders konsol
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

def run_training_generator():
    """Kör ditt CLI-skript i bakgrunden"""
    logger.info("🚀 Startar AI-träningsgeneratorn via webhook...")
    try:
        # Vi kör ditt originalskript med --auto flaggan
        # Använd sys.executable för att garantera att rätt Python-miljö används
        import sys
        result = subprocess.run(
            [sys.executable, "training_plan_generator.py", "--auto"], 
            capture_output=True, 
            text=True
        )
        
        if result.returncode == 0:
            logger.info("✅ Generatorn kördes framgångsrikt!")
            # Skriv ut skriptets loggar så vi kan se vad AI:n gjorde
            for line in result.stdout.split('\n'):
                if line: logger.info(f"  [AI]: {line}")
        else:
            logger.error("❌ Generatorn kraschade.")
            logger.error(result.stderr)
            
    except Exception as e:
        logger.error(f"Ett fel inträffade när skriptet skulle startas: {e}")


@app.route('/webhook', methods=['POST'])
def intervals_webhook():
    """Endpoint som Intervals.icu skickar data till"""
    data = request.json
    logger.info(f"📥 Tog emot webhook från Intervals: {data}")

    # AI-modellerna kan ta 10-30 sekunder på sig att svara.
    # Webhooks vill ha svar (200 OK) direkt, annars tror de att servern är trasig.
    # Därför startar vi skriptet i en separat tråd och svarar Intervals direkt!
    thread = threading.Thread(target=run_training_generator)
    thread.start()
    
    return jsonify({
        "status": "success", 
        "message": "Webhook mottagen. Träningsplan genereras i bakgrunden."
    }), 200


@app.route('/', methods=['GET'])
def health_check():
    """Enkel sida för att se att servern är vaken (används av Render)"""
    return "AI-Träningsgenerator är online och redo! 🏃‍♂️🤖", 200


if __name__ == '__main__':
    # Hämta port från miljövariabel (krävs för molntjänster som Render)
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)