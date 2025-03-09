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
    'user': '*******',
    'password': '*******',
    'host': '127.0.0.1',  # Tunnel locale
    'port': None,  # Da assegnare dinamicamente
    'database': 'scorpionero$tokens',
    'charset': 'utf8mb4'
}

SSH_CONFIG = {
    'host': 'ssh.pythonanywhere.com',
    'username': '*******',
    'password': '*******-',
    'remote_bind_host': 'scorpionero.mysql.pythonanywhere-services.com',
    'remote_bind_port': 3306
}

ssh_tunnel = None
import paramiko
from sshtunnel import SSHTunnelForwarder
import time

def maintain_ssh_tunnel():
    global ssh_tunnel
    retry_attempts = 5
    attempt = 0
    
    while attempt < retry_attempts:
        try:
            logger.debug("Tentativo di creazione del tunnel SSH...")
            if ssh_tunnel is None or not ssh_tunnel.is_active:
                # Crea il tunnel SSH
                ssh_tunnel = SSHTunnelForwarder(
                    (SSH_CONFIG['host'], 22),
                    ssh_username=SSH_CONFIG['username'],
                    ssh_password=SSH_CONFIG['password'],
                    remote_bind_address=(SSH_CONFIG['remote_bind_host'], SSH_CONFIG['remote_bind_port']),
                    local_bind_address=('127.0.0.1', 0)  # Porta dinamica
                )
                # Avvia il tunnel SSH
                ssh_tunnel.start()
                ssh_tunnel._client.set_keepalive(60)  # Mantenere il tunnel attivo con pacchetti keep-alive ogni 60 secondi

                # Verifica se la porta locale è stata assegnata
                if ssh_tunnel.local_bind_port is None:
                    raise ValueError("La porta locale del tunnel SSH non è stata assegnata correttamente.")
                
                # Aggiorna la configurazione della porta nel MYSQL_CONFIG
                MYSQL_CONFIG['port'] = ssh_tunnel.local_bind_port
                logger.info(f"Tunnel SSH aperto con successo sulla porta {MYSQL_CONFIG['port']}")
                return  # Tunnel stabilito con successo
            else:
                logger.debug("Tunnel SSH già attivo.")
                return
        except Exception as e:
            logger.error(f"Errore nell'aprire il tunnel SSH: {e}")
            attempt += 1
            if attempt < retry_attempts:
                logger.info(f"Riprovo a creare il tunnel SSH... (tentativo {attempt}/{retry_attempts})")
                time.sleep(5)  # Attendi 5 secondi prima di ritentare
            else:
                logger.critical("Numero massimo di tentativi raggiunto per aprire il tunnel SSH.")
                raise


# Funzione per verificare periodicamente lo stato del tunnel SSH
def check_ssh_tunnel():
    while True:
        if ssh_tunnel is None or not ssh_tunnel.is_active:
            logger.info("Tunnel SSH non attivo. Tentando di ripristinarlo...")
            maintain_ssh_tunnel()
        time.sleep(60)  # Controlla ogni 60 secondi

def connect_to_db():
    global ssh_tunnel
    try:
        if ssh_tunnel is None or not ssh_tunnel.is_active:
            maintain_ssh_tunnel()  # Assicura che il tunnel SSH sia attivo

        # Verifica che la porta locale sia assegnata correttamente
        if MYSQL_CONFIG['port'] is None:
            logger.error("La porta locale del tunnel SSH non è stata assegnata correttamente. Riprovo...")
            maintain_ssh_tunnel()  # Forza il tentativo di riapertura del tunnel
            if MYSQL_CONFIG['port'] is None:
                raise ValueError("Impossibile assegnare la porta locale per MySQL.")
        
        logger.debug(f"Connettendo al database sulla porta {MYSQL_CONFIG['port']}...")

        # Connessione al database
        connection = mysql.connector.connect(**MYSQL_CONFIG)
        logger.info("Connessione al database stabilita con successo.")
        return connection
    except mysql.connector.Error as e:
        logger.error(f"Errore nella connessione al database: {e}")
        return None
    except Exception as e:
        logger.error(f"Errore inaspettato durante la connessione al database: {e}")
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
                
                # Disabilita temporaneamente il logging
                logger.disabled = True
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
    threading.Thread(target=maintain_ssh_tunnel, daemon=True).start()
    threading.Thread(target=check_ssh_tunnel, daemon=True).start()

    # Avvia il thread per inviare i dati ai client
    threading.Thread(target=send_data_to_clients, daemon=True).start()

@socketio.on('connect')
def handle_connect():
    logger.info("Client connesso")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info("Client disconnesso")

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
