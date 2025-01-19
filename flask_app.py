# Patching di Gevent
monkey.patch_all()

from flask_socketio import SocketIO, emit
from flask import Flask, render_template
from flask_cors import CORS
import time
import threading
import mysql.connector
from mysql.connector import pooling
from decimal import Decimal
import logging

from collections import Counter
import json

# Definisci async_mode
async_mode = "gevent"

app = Flask(__name__)
CORS(app)

socketio = SocketIO(app, async_mode=async_mode, cors_allowed_origins="*", logger=True, engineio_logger=True, ping_interval=60, ping_timeout=120)
print(f"Async mode in use: {socketio.server.eio.async_mode}")

# Configurazione logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/scorpionero/unico/log/application.log', mode='a', encoding='utf-8', delay=False),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
app.logger.setLevel(logging.DEBUG)

# Configurazione database
MYSQL_CONFIG = {
    'user': 'scorpionero',
    'password': 'MYSQL123-',
    'host': 'scorpionero.mysql.pythonanywhere-services.com',
    'port': 3306,
    'database': 'scorpionero$tokens',
    'charset': 'utf8mb4'
}

# Cache per confrontare i dati
last_data = None

try:
    connection_pool = pooling.MySQLConnectionPool(
        pool_name="mypool",
        pool_size=5,
        **MYSQL_CONFIG
    )
    logger.info("Connection pool configurato con successo.")
except mysql.connector.Error as err:
    logger.critical(f"Errore nella configurazione del connection pool: {err}")
    raise

def fetch_data():
    db = None
    cursor = None
    try:
        logger.debug("Tentativo di recuperare dati dal database.")

        # Ottieni una connessione dal pool
        db = connection_pool.get_connection()
        cursor = db.cursor(dictionary=True)  # Risultati come dizionari

        # Seleziona il database e recupera i dati
        cursor.execute("USE scorpionero$tokens")
        cursor.execute("SELECT mint, ticker, `signals`, score, market_cap, update_time FROM coin_signals ORDER BY score DESC LIMIT 24")
        tokens_data = cursor.fetchall()

        # Struttura finale dei dati
        data = {}

        # Elaborazione dei dati
        for token in tokens_data:
            mint = token['mint']
            ticker = token['ticker']
            signals = json.loads(token['signals'])  # Converti signals in lista
            score = token['score']
            market_cap = token['market_cap']
            update_time = token['update_time']

            # Normalizza i segnali
            signals = [signal.strip().lower() for signal in signals]

            # Conta i segnali
            signals_count = Counter(signals)

            # Conteggi separati
            buy_signals = signals_count.get("buy", 0)
            sell_signals = signals_count.get("sell", 0)
            extra_signals = signals_count.get("extra", 0)

            total_signals = buy_signals + sell_signals

            # Crea una nuova struttura con i dati aggiornati
            realtime_data = {
                'ticker': ticker,
                'buy_signals': buy_signals,
                'sell_signals': sell_signals,
                'extra_signals': extra_signals,
                'total_signals': total_signals,
                'score': score,
                'market_cap': market_cap,
                'update_time': update_time,
            }

            # Inizializza la struttura per ogni token
            if mint not in data:
                data[mint] = {
                    "realtime_data": [],
                    "historical_data": []
                }

            # Aggiungi i dati in tempo reale
            data[mint]["realtime_data"].append(realtime_data)

            # Recupera i dati storici per ogni token
            cursor.execute("""
                SELECT *
                FROM market_cap_history
                WHERE mint = %s
                AND update_time > UNIX_TIMESTAMP(NOW()) - 600
                ORDER BY update_time ASC;
            """, (mint,))
            market_cap_history = cursor.fetchall()
            logger.debug(f"Dati storici per mint '{mint}': {market_cap_history}")

            # Aggiungi i dati storici alla struttura
            data[mint]["historical_data"].extend([
                {
                    "trader": row["trader"],
                    "signal": row["signal"],
                    "speed": row["speed"],
                    "score": row["score"],
                    "score_tot": row["score_tot"],
                    "solAmount": row["solAmount"],
                    "vTokensInBondingCurve": row["vTokensInBondingCurve"],
                    "sol_in_bonding": row["sol_in_bonding"],
                    "new_token_balance": row["new_token_balance"],
                    "market_cap": row["market_cap"],
                    "update_time": row["update_time"]
                }
                for row in market_cap_history
            ])



        logger.debug(f"Dati recuperati per {len(data)} token.")
        return data

    except mysql.connector.Error as err:
        logger.error(f"Errore MySQL: {err}")
        return {}

    finally:
        # Chiudi cursore e connessione
        if cursor:
            cursor.close()
        if db:
            db.close()  # Rilascia la connessione al pool

def safe_convert_to_float(data):
    """
    Converte tutti i valori nei dati in tipi compatibili con JSON.
    Converte anche Decimal in float.

    Args:
        data (dict): Dati strutturati contenenti informazioni miste.

    Returns:
        dict: Dati con tutti i valori convertiti in tipi compatibili con JSON.
    """
    def convert_value(value):
        # Converte numeri, Decimal in float, e lascia gli altri valori invariati
        if isinstance(value, (int, float, Decimal)):
            return float(value)  # Converti Decimal in float
        elif isinstance(value, (list, tuple)):
            return [convert_value(v) for v in value]  # Recursivamente converte gli elementi di lista/tupla
        elif isinstance(value, dict):
            return {k: convert_value(v) for k, v in value.items()}  # Recursivamente converte gli elementi di dizionario
        else:
            return value  # Altri tipi (str, None, ecc.) rimangono invariati

    return {token: {key: convert_value(details[key]) for key in details} for token, details in data.items()}




def send_data_to_clients():
    global last_data
    while True:
        try:
            logger.debug("Invio dati ai client...")
            data = fetch_data()

            # Convertire i dati condizionalmente
            data = safe_convert_to_float(data)

            # Invio dei dati ai client con SocketIO
            socketio.emit('update', {"data": data})

            logger.info("Dati aggiornati inviati ai client.")

            time.sleep(0.1)

        except Exception as e:
            logger.error(f"Errore durante l'invio dei dati: {e}")
            time.sleep(5)  # Riprova dopo un errore


@app.route('/')
def index():
    logger.info("Pagina principale servita.")
    return render_template('index.html')

@app.before_first_request
def before_first_request():
    logger.info("Inizializzazione del thread per l'invio in tempo reale.")
    thread = threading.Thread(target=send_data_to_clients)
    thread.daemon = True
    thread.start()

@socketio.on('connect')
def handle_connect():
    logger.info("Client connesso.")
    socketio.emit('status', {'message': 'Connessione avvenuta con successo'})
    emit('pong', {'data': 'pong'})

def send_ping():
    while True:
        time.sleep(10)
        socketio.emit('ping', {'data': 'ping'})

@socketio.on('pong')
def handle_pong(message):
    logger.debug("Ricevuto pong dal client: %s", message)

@socketio.on('message')
def handle_message(msg):
    logger.info(f"Messaggio ricevuto dal client: {msg}")
    socketio.send("Risposta dal server!")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info("Client disconnesso.")

@socketio.on_error_default
def default_error_handler(e):
    logger.error(f"Errore Socket.IO: {e}")

if __name__ == '__main__':

    logger.info("Avvio del server Flask-SocketIO.")
    socketio.start_background_task(target=send_ping)
    try:
        socketio.run(app, host='0.0.0.0', port=8081)
    except Exception as e:
        logger.error(f"Errore nell'avvio del server: {e}")
