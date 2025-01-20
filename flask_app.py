import os
from flask_socketio import SocketIO, emit
from flask import Flask, render_template
from flask_cors import CORS
import time
import threading
import mysql.connector
from decimal import Decimal
import logging
import sshtunnel
import json

ssh_tunnel = None

# Configurazione logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)
app = Flask(__name__)
CORS(app)

# Configurazione SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True, ping_interval=60, ping_timeout=120)

# Dati di configurazione per MySQL e SSH
MYSQL_CONFIG = {
    'user': 'scorpionero',
    'password': 'MYSQL123-',
    'host': '127.0.0.1',
    'port': 3306,
    'database': 'scorpionero$tokens',
    'charset': 'utf8mb4'
}

SSH_HOST = 'ssh.pythonanywhere.com'
SSH_USERNAME = 'scorpionero'
SSH_PASSWORD = 'Mayhem123-'
DB_HOST = 'scorpionero.mysql.pythonanywhere-services.com'

# Funzione per stabilire la connessione al database
def connect_to_db(ssh_tunnel):
    if ssh_tunnel is None or not ssh_tunnel.is_alive():
        logger.error("Il tunnel SSH non è attivo.")
        return None

    try:
        connection = mysql.connector.connect(
            user=MYSQL_CONFIG['user'],
            password=MYSQL_CONFIG['password'],
            host='127.0.0.1',
            port=ssh_tunnel.local_bind_port,
            database=MYSQL_CONFIG['database'],
            charset=MYSQL_CONFIG['charset']
        )
        logger.info("Connessione al database riuscita.")
        return connection
    except mysql.connector.Error as err:
        logger.error(f"Errore MySQL nella connessione: {err}")
        return None
    except Exception as e:
        logger.error(f"Errore nella connessione al tunnel SSH o DB: {e}")
        return None

# Funzione per stabilire e mantenere il tunnel SSH aperto
def maintain_ssh_tunnel():
    global ssh_tunnel
    while True:
        try:
            logger.debug("Tentativo di creazione del tunnel SSH...")
            ssh_tunnel = sshtunnel.SSHTunnelForwarder(
                (SSH_HOST),
                ssh_username=SSH_USERNAME,
                ssh_password=SSH_PASSWORD,
                remote_bind_address=(DB_HOST, 3306)
            )
            ssh_tunnel.start()
            logger.info("Tunnel SSH aperto con successo.")

            logger.debug(f"Tipo di 'ssh_tunnel': {type(ssh_tunnel)}")
            logger.debug(f"Stato del tunnel: {ssh_tunnel.is_alive()}")
            logger.debug(f"Porta locale del tunnel: {ssh_tunnel.local_bind_port}")
            break  # Se il tunnel è aperto correttamente, esci dal ciclo
        except Exception as e:
            logger.error(f"Errore nell'aprire il tunnel SSH: {e}")
            logger.debug(f"Tipo di 'ssh_tunnel' al momento dell'errore: {type(ssh_tunnel)}")
            logger.debug(f"Valore di 'ssh_tunnel': {ssh_tunnel}")
            time.sleep(5)  # Pausa prima di tentare di nuovo

# Funzione per ottenere i dati dal database
def fetch_data():
    global ssh_tunnel
    connection = connect_to_db(ssh_tunnel)
    if not connection:
        logger.error("Impossibile connettersi al database. Riprovo tra pochi secondi...")
        return {}

    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT mint, ticker, `signals`, score, market_cap, update_time FROM coin_signals ORDER BY score DESC LIMIT 24")
        tokens_data = cursor.fetchall()

        data = {}
        for token in tokens_data:
            mint = token['mint']
            ticker = token['ticker']
            signals = json.loads(token['signals'])
            score = token['score']
            market_cap = token['market_cap']
            update_time = token['update_time']

            signals_count = {"buy": 0, "sell": 0, "extra": 0}
            for signal in signals:
                signals_count[signal.strip().lower()] += 1

            realtime_data = {
                'ticker': ticker,
                'buy_signals': signals_count['buy'],
                'sell_signals': signals_count['sell'],
                'extra_signals': signals_count['extra'],
                'total_signals': sum(signals_count.values()),
                'score': score,
                'market_cap': market_cap,
                'update_time': update_time,
            }

            if mint not in data:
                data[mint] = {
                    "realtime_data": [],
                    "historical_data": []
                }

            data[mint]["realtime_data"].append(realtime_data)

            cursor.execute("""
                SELECT * FROM market_cap_history WHERE mint = %s AND update_time > UNIX_TIMESTAMP(NOW()) - 600 ORDER BY update_time ASC;
            """, (mint,))
            market_cap_history = cursor.fetchall()
            for row in market_cap_history:
                data[mint]["historical_data"].append(row)

        return data
    except mysql.connector.Error as err:
        logger.error(f"Errore MySQL durante la query: {err}")
        return {}
    finally:
        cursor.close()
        connection.close()

# Funzione per inviare i dati ai client ogni 0.5 secondi
def send_data_to_clients():
    while True:
        try:
            data = fetch_data()
            if data:
                socketio.emit('update', {"data": data})
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Errore durante l'invio dei dati: {e}")
            time.sleep(5)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/test_tunnel')
def test_tunnel():
    global ssh_tunnel
    try:
        if ssh_tunnel and ssh_tunnel.is_alive():
            return f"Tunnel attivo sulla porta {ssh_tunnel.local_bind_port}", 200
        else:
            return "Tunnel non attivo", 500
    except Exception as e:
        logger.error(f"Errore nel test_tunnel: {e}")
        return f"Errore: {e}", 500

@app.before_first_request
def before_first_request():
    # Avvia il tunnel SSH in un thread separato
    ssh_thread = threading.Thread(target=maintain_ssh_tunnel)
    ssh_thread.daemon = True
    ssh_thread.start()

    # Avvia il thread per inviare i dati ai client
    thread = threading.Thread(target=send_data_to_clients)
    thread.daemon = True
    thread.start()

@socketio.on('connect')
def handle_connect():
    emit('status', {'message': 'Connessione avvenuta con successo'})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8081)
