#!/usr/bin/env python3
import os
import json
import email
import imaplib
import logging
import re
import requests
import platform
from pathlib import Path
from email.header import decode_header
from dotenv import load_dotenv
from bs4 import BeautifulSoup
# =========================
# CONFIGURAÇÃO DE CAMINHOS
# =========================
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
LOG_FILE = BASE_DIR / "email_telegram.log"
LOCK_FILE = BASE_DIR / "email_telegram.lock"
STATE_FILE = BASE_DIR / "processed_uids.json"
USERS_FILE = BASE_DIR / "usuarios.json"  # 🔥 NOVO

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
# 🔥 NOVAS FUNÇÕES (multiusuário)
# =========================
def carregar_usuarios():
    if not USERS_FILE.exists():
        return []
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def salvar_usuarios(usuarios):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(usuarios, f, indent=2)


def capturar_usuarios():
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    response = requests.get(url, timeout=10).json()

    usuarios = carregar_usuarios()

    for update in response.get("result", []):
        try:
            mensagem = update["message"]["text"]
            chat_id = update["message"]["chat"]["id"]

            if mensagem == "/start" and chat_id not in usuarios:
                usuarios.append(chat_id)
                logger.info(f"Novo usuário: {chat_id}")

        except:
            continue

    salvar_usuarios(usuarios)

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
    html_content = None
    text_content = None

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "").lower()

            if "attachment" in disposition:
                continue

            payload = part.get_payload(decode=True)
            charset = part.get_content_charset() or "utf-8"

            if not payload:
                continue

            try:
                decoded = payload.decode(charset, errors="replace")
            except:
                decoded = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain":
                text_content = decoded

            elif content_type == "text/html":
                html_content = decoded

    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"

        if payload:
            try:
                decoded = payload.decode(charset, errors="replace")
            except:
                decoded = payload.decode("utf-8", errors="replace")

            if msg.get_content_type() == "text/plain":
                text_content = decoded
            else:
                html_content = decoded

    # 🔥 PRIORIDADE: usar texto puro se existir
    if text_content:
        return text_content.strip()

    # 🔥 SENÃO: limpar HTML
    if html_content:
        soup = BeautifulSoup(html_content, "html.parser")

        # remove lixo: scripts, estilos, head, imagens, inputs, iframes
        for tag in soup(["script", "style", "head", "img", "input",
                         "iframe", "noscript", "svg", "meta", "link"]):
            tag.decompose()

        # remove elementos ocultos (display:none, hidden, width/height 0/1)
        for tag in soup.find_all(True):
            style = (tag.get("style") or "").lower()
            if "display:none" in style or "display: none" in style:
                tag.decompose()
                continue
            if tag.get("hidden") is not None:
                tag.decompose()
                continue
            # tracking pixels e spacers (tabelas 1x1, etc)
            w = tag.get("width", "")
            h = tag.get("height", "")
            if w in ("0", "1") or h in ("0", "1"):
                tag.decompose()
                continue

        # links → texto (url) — só exibe URL se for http e diferente do texto
        for a in soup.find_all("a", href=True):
            texto_link = a.get_text(strip=True)
            href = a["href"].strip()
            if not href.startswith(("http://", "https://")):
                a.replace_with(texto_link)
            elif texto_link and texto_link != href:
                a.replace_with(f"{texto_link} ({href})")
            elif href:
                a.replace_with(href)

        texto = soup.get_text(separator="\n")

        # limpa espaços por linha e colapsa linhas vazias
        linhas = [linha.strip() for linha in texto.splitlines()]
        # remove linhas duplicadas consecutivas em branco
        resultado = []
        for linha in linhas:
            if linha == "" and (not resultado or resultado[-1] == ""):
                continue
            resultado.append(linha)

        return "\n".join(resultado).strip()

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
        if len(linha) > limite:
            # Linha maior que o limite: quebra no meio
            if atual:
                blocos.append(atual)
                atual = ""
            while len(linha) > limite:
                blocos.append(linha[:limite])
                linha = linha[limite:]
            if linha:
                atual = linha
        elif len(atual) + len(linha) > limite:
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


# 🔥 ALTERADO (multiusuário)
def enviar_telegram(mensagem):
    usuarios = carregar_usuarios()

    if not usuarios and CHAT_ID:
        usuarios = [CHAT_ID]

    if not usuarios:
        logger.warning("Nenhum usuário cadastrado.")
        return

    for chat_id in usuarios:
        try:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            dados = {
                "chat_id": chat_id,
                "text": mensagem,
            }

            resp = requests.post(url, data=dados, timeout=(5, 20))
            resp.raise_for_status()

            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Erro retornado pelo Telegram: {data}")

        except Exception as e:
            logger.error(f"Erro ao enviar para {chat_id}: {e}")


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
    capturar_usuarios()  # 🔥 NOVO

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
                corpo = extrair_corpo_texto(msg)

                mensagem = (
                    "📧 Novo e-mail recebido\n\n"
                    f"De: {remetente}\n"
                    f"Assunto: {assunto or '(sem assunto)'}\n\n"
                    f"{corpo or '(sem corpo em texto)'}"
                )

                blocos = dividir_em_blocos(mensagem)
                total = len(blocos)
                for i, bloco in enumerate(blocos, 1):
                    if total > 1:
                        bloco = f"[{i}/{total}]\n{bloco}"
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