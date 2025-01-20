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
from sshtunnel import SSHTunnelForwarder
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
            # Avvia il tunnel
            ssh_tunnel.start()
            logger.info("Tunnel SSH aperto con successo.")
            logger.debug(f"Porta locale del tunnel: {ssh_tunnel.local_bind_port}")
        except Exception as e:
            logger.error(f"Errore nell'aprire il tunnel SSH: {e}")
            raise
    else:
        logger.debug("Tunnel SSH già attivo.")

# Funzione per stabilire la connessione al database
# Funzione per creare il tunnel SSH e connettersi al database
def connect_to_db():
    try:
        with SSHTunnelForwarder(
            ('ssh.pythonanywhere.com', 22),
            ssh_username='your_username', 
            ssh_password='your_password',
            remote_bind_address=('scorpionero.mysql.pythonanywhere-services.com', 3306)
        ) as tunnel:
            time.sleep(5)  # Aggiungi un breve ritardo per garantire che il tunnel sia pronto
            connection = mysql.connector.connect(
                user='your_db_user',
                password='your_db_password',
                host='127.0.0.1',
                port=tunnel.local_bind_port,
                database='your_db_name'
            )
            return connection
    except Exception as e:
        print(f"Errore nella connessione al database: {e}")
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
                logger.disabled = True  # Disabilita temporaneamente il logging
                socketio.emit('update', {"data": data})
                logger.disabled = False  # Riabilita il logging
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

    # Aspetta che il tunnel sia pronto prima di avviare il thread dei dati
    tunnel_thread.join()  # Aspetta che il tunnel sia avviato

    # Avvia il thread per inviare i dati ai client
    data_thread = threading.Thread(target=send_data_to_clients)
    data_thread.daemon = True
    data_thread.start()


@socketio.on('connect')
def handle_connect():
    logger.info("Client connesso")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info("Client disconnesso")

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
