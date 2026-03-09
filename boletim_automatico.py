import imaplib
import email
from email.header import decode_header
import requests
import os
from dotenv import load_dotenv

load_dotenv()

EMAIL = os.getenv("EMAIL_GMAIL")
SENHA = os.getenv("SENHA_GMAIL")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def enviar_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    dados = {
        "chat_id": CHAT_ID,
        "text": mensagem
    }

    requests.post(url, data=dados)


mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
mail.login(EMAIL, SENHA)
mail.select("inbox")

status, messages = mail.search(None, "UNSEEN")

for num in messages[0].split():

    status, data = mail.fetch(num, "(RFC822)")
    raw_email = data[0][1]

    msg = email.message_from_bytes(raw_email)

    subject, encoding = decode_header(msg["Subject"])[0]
    if isinstance(subject, bytes):
        subject = subject.decode(encoding if encoding else "utf-8")

    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode()
                break
    else:
        body = msg.get_payload(decode=True).decode()

    mensagem = f"📧 Novo email recebido\n\nAssunto: {subject}\n\n{body}"

    print(mensagem)

    enviar_telegram(mensagem)

mail.logout()

print("Verificação concluída.")