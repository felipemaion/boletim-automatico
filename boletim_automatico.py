#!/usr/bin/env python3
import os
import json
import email
import imaplib
import logging
import requests
import platform
from pathlib import Path
from email.header import decode_header
from dotenv import load_dotenv

# =========================
# CONFIGURAÇÃO DE CAMINHOS
# =========================
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
LOG_FILE = BASE_DIR / "email_telegram.log"
LOCK_FILE = BASE_DIR / "email_telegram.lock"
STATE_FILE = BASE_DIR / "processed_uids.json"

load_dotenv(ENV_FILE)

EMAIL = os.getenv("EMAIL_GMAIL")
SENHA = os.getenv("SENHA_GMAIL")  # use senha de app
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

TELEGRAM_MSG_LIMIT = 4000
BODY_PREVIEW_LIMIT = 1200
MAX_STORED_UIDS = 5000

# =========================
# LOCK BACKEND
# =========================
IS_WINDOWS = platform.system() == "Windows"

try:
    if IS_WINDOWS:
        raise ImportError("Forçando fallback para filelock no Windows")
    import fcntl
    LOCK_BACKEND = "fcntl"
except ImportError:
    try:
        from filelock import FileLock, Timeout
        LOCK_BACKEND = "filelock"
    except ImportError:
        raise RuntimeError(
            "Nenhum backend de lock disponível. "
            "No Linux/macOS use fcntl nativo. "
            "No Windows instale 'filelock'."
        )

# =========================
# LOG
# =========================
logger = logging.getLogger("email_telegram")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    "%Y-%m-%d %H:%M:%S"
)

if not logger.handlers:
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

# =========================
# UTILITÁRIOS
# =========================
def validar_env():
    faltando = []
    for nome, valor in {
        "EMAIL_GMAIL": EMAIL,
        "SENHA_GMAIL": SENHA,
        "TELEGRAM_TOKEN": TOKEN,
        "TELEGRAM_CHAT_ID": CHAT_ID,
    }.items():
        if not valor:
            faltando.append(nome)

    if faltando:
        raise RuntimeError(f"Variáveis ausentes no .env: {', '.join(faltando)}")


def decodificar_cabecalho(valor):
    if not valor:
        return ""
    partes = decode_header(valor)
    resultado = []
    for parte, encoding in partes:
        if isinstance(parte, bytes):
            try:
                resultado.append(parte.decode(encoding or "utf-8", errors="replace"))
            except Exception:
                resultado.append(parte.decode("utf-8", errors="replace"))
        else:
            resultado.append(parte)
    return "".join(resultado).strip()


def extrair_corpo_texto(msg):
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "").lower()

            if "attachment" in disposition:
                continue

            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                if payload:
                    return payload.decode(charset, errors="replace").strip()

        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                if payload:
                    return payload.decode(charset, errors="replace").strip()
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        if payload:
            return payload.decode(charset, errors="replace").strip()

    return ""


def resumir_texto(texto, limite=BODY_PREVIEW_LIMIT):
    texto = " ".join(texto.split())
    if len(texto) <= limite:
        return texto
    return texto[:limite - 3] + "..."


def dividir_em_blocos(texto, limite=TELEGRAM_MSG_LIMIT):
    blocos = []
    atual = ""

    for linha in texto.splitlines(True):
        if len(atual) + len(linha) > limite:
            if atual:
                blocos.append(atual)
            atual = linha
        else:
            atual += linha

    if atual:
        blocos.append(atual)

    if not blocos and texto:
        blocos = [texto[:limite]]

    return blocos


def enviar_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    dados = {
        "chat_id": CHAT_ID,
        "text": mensagem,
    }

    resp = requests.post(url, data=dados, timeout=(5, 20))
    resp.raise_for_status()

    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Erro retornado pelo Telegram: {data}")


def carregar_uids_processados():
    if not STATE_FILE.exists():
        return []

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Não foi possível ler {STATE_FILE.name}: {e}")
        return []


def salvar_uids_processados(uids):
    try:
        uids = uids[-MAX_STORED_UIDS:]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(uids, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Falha ao salvar {STATE_FILE.name}: {e}")
        raise

# =========================
# LOCK ABSTRATO
# =========================
class SingleInstanceLock:
    def __init__(self, path):
        self.path = str(path)
        self.handle = None
        self.filelock = None

    def __enter__(self):
        if LOCK_BACKEND == "fcntl":
            self.handle = open(self.path, "w", encoding="utf-8")
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.handle.write(str(os.getpid()))
                self.handle.flush()
                logger.info("Lock adquirido com fcntl.")
                return self
            except BlockingIOError:
                self.handle.close()
                raise RuntimeError("Outra instância do script já está em execução.")
        else:
            self.filelock = FileLock(self.path + ".winlock", timeout=0)
            try:
                self.filelock.acquire()
                logger.info("Lock adquirido com filelock.")
                return self
            except Timeout:
                raise RuntimeError("Outra instância do script já está em execução.")

    def __exit__(self, exc_type, exc, tb):
        try:
            if LOCK_BACKEND == "fcntl" and self.handle:
                self.handle.seek(0)
                self.handle.truncate()
                fcntl.flock(self.handle, fcntl.LOCK_UN)
                self.handle.close()
            elif LOCK_BACKEND == "filelock" and self.filelock:
                self.filelock.release()
        except Exception:
            pass

# =========================
# IMAP
# =========================
def buscar_uids_nao_lidos(mail):
    status, data = mail.uid("search", None, "UNSEEN")
    if status != "OK":
        raise RuntimeError("Falha ao buscar UIDs de e-mails não lidos.")
    return data[0].split()


def buscar_email_por_uid(mail, uid):
    status, data = mail.uid("fetch", uid, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        raise RuntimeError(f"Falha ao buscar e-mail UID {uid.decode()}.")
    return data[0][1]


def processar():
    validar_env()

    uids_processados = carregar_uids_processados()
    uids_processados_set = set(uids_processados)

    logger.info(f"Iniciando verificação de e-mails. Backend de lock: {LOCK_BACKEND}")

    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        mail.login(EMAIL, SENHA)
        mail.select("inbox")

        uids = buscar_uids_nao_lidos(mail)

        if not uids:
            logger.info("Nenhum e-mail não lido encontrado.")
            return

        logger.info(f"{len(uids)} e-mail(s) não lido(s) encontrado(s).")

        novos_processados = 0

        for uid_bytes in uids:
            uid = uid_bytes.decode()

            if uid in uids_processados_set:
                logger.info(f"UID {uid} já processado anteriormente. Ignorando.")
                continue

            try:
                raw_email = buscar_email_por_uid(mail, uid_bytes)
                msg = email.message_from_bytes(raw_email)

                remetente = decodificar_cabecalho(msg.get("From"))
                assunto = decodificar_cabecalho(msg.get("Subject"))
                corpo = resumir_texto(extrair_corpo_texto(msg))

                mensagem = (
                    "📧 Novo e-mail recebido\n\n"
                    f"De: {remetente}\n"
                    f"Assunto: {assunto or '(sem assunto)'}\n\n"
                    f"{corpo or '(sem corpo em texto)'}"
                )

                for bloco in dividir_em_blocos(mensagem):
                    enviar_telegram(bloco)

                uids_processados.append(uid)
                uids_processados_set.add(uid)
                novos_processados += 1

                logger.info(f"UID {uid} enviado ao Telegram com sucesso. Assunto: {assunto}")

            except Exception as e:
                logger.exception(f"Erro ao processar UID {uid}: {e}")

        salvar_uids_processados(uids_processados)
        logger.info(f"Processamento concluído. Novos enviados: {novos_processados}")

    finally:
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        with SingleInstanceLock(LOCK_FILE):
            processar()
    except RuntimeError as e:
        logger.warning(str(e))
    except Exception as e:
        logger.exception(f"Erro fatal: {e}")
        raise
