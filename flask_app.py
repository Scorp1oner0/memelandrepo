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

ssh_tunnel = None
db_connection = None


# Funzione per stabilire e mantenere il tunnel SSH aperto
def maintain_ssh_tunnel():
    global ssh_tunnel
    if ssh_tunnel is None:
        try:
            logger.debug("Tentativo di creazione del tunnel SSH...")
            ssh_tunnel = sshtunnel.SSHTunnelForwarder(
                (SSH_HOST),
                ssh_username=SSH_USERNAME,
                ssh_password=SSH_PASSWORD,
                remote_bind_address=(DB_HOST, 3306),
                # Keep-alive per evitare la chiusura
                local_bind_address=('0.0.0.0', 0)  # Assegna automaticamente la porta locale
            )
            # Avvia il tunnel in modo da rimanere attivo
            ssh_tunnel.start()
            logger.info("Tunnel SSH aperto con successo.")
            logger.debug(f"Porta locale del tunnel: {ssh_tunnel.local_bind_port}")

            # Ciclo per mantenere il tunnel attivo
            while True:
                time.sleep(60)  # Pausa per 60 secondi, evita chiusura automatica
                logger.debug("Tunnel SSH mantenuto attivo...")

        except Exception as e:
            logger.error(f"Errore nell'aprire il tunnel SSH: {e}")
            raise

# Funzione per stabilire la connessione al database
def connect_to_db():
    global ssh_tunnel
    global db_connection
    if db_connection:
        return db_connection

    if ssh_tunnel is None or not hasattr(ssh_tunnel, 'local_bind_port') or ssh_tunnel.local_bind_port is None:
        logger.error("Il tunnel SSH non è attivo. Tentativo di riaprirlo.")
        maintain_ssh_tunnel()  # Riapri il tunnel se non è attivo
        if ssh_tunnel is None or not hasattr(ssh_tunnel, 'local_bind_port') or ssh_tunnel.local_bind_port is None:
            logger.error("Il tunnel SSH non è riuscito a connettersi.")
            return None

    try:
        db_connection = mysql.connector.connect(
            user=MYSQL_CONFIG['user'],
            password=MYSQL_CONFIG['password'],
            host='127.0.0.1',
            port=ssh_tunnel.local_bind_port,
            database=MYSQL_CONFIG['database'],
            charset=MYSQL_CONFIG['charset']
        )
        logger.info("Connessione al database riuscita.")
        return db_connection
    except mysql.connector.Error as err:
        logger.error(f"Errore MySQL nella connessione: {err}")
        return None
    except Exception as e:
        logger.error(f"Errore nella connessione al tunnel SSH o DB: {e}")
        return None

# Funzione per ottenere i dati dal database
def fetch_data():
    connection = connect_to_db()
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
                signal = signal.strip().lower()  # rimuove spazi e converte in minuscolo
                if signal != 'new':  # Ignora 'new'
                    signals_count[signal] += 1
                
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

# Funzione di supporto per convertire Decimal in float
def convert_decimal_to_float(data):
    if isinstance(data, Decimal):
        return float(data)  # Converte Decimal in float
    elif isinstance(data, dict):
        return {key: convert_decimal_to_float(value) for key, value in data.items()}  # Converte ricorsivamente nel dizionario
    elif isinstance(data, list):
        return [convert_decimal_to_float(item) for item in data]  # Converte ricorsivamente nella lista
    else:
        return data  # Non converte altri tipi di dati

# Funzione per inviare i dati ai client ogni 0.5 secondi
def send_data_to_clients():
    while True:
        try:
            data = fetch_data()
            if data:
                # Applica la conversione a livello profondo
                data = convert_decimal_to_float(data)
                logger.debug("Invio dati ai client")
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
    if ssh_tunnel and hasattr(ssh_tunnel, 'local_bind_port') and ssh_tunnel.local_bind_port:
        return f"Tunnel attivo sulla porta {ssh_tunnel.local_bind_port}", 200
    else:
        return "Tunnel non attivo", 500

@app.before_first_request
def before_first_request():
    # Avvia il tunnel SSH in un thread separato
    tunnel_thread = threading.Thread(target=maintain_ssh_tunnel)
    tunnel_thread.daemon = True
    tunnel_thread.start()

    # Avvia il thread per inviare i dati ai client
    data_thread = threading.Thread(target=send_data_to_clients)
    data_thread.daemon = True
    data_thread.start()


@socketio.on('connect')
def handle_connect():
    emit('status', {'message': 'Connessione avvenuta con successo'})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8081)

