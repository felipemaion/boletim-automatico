# Boletim Automático

Bot que verifica emails do Gmail e envia automaticamente para o Telegram.

Bot Telegram:
@boletim_automatico_bot

Funcionalidades

- conecta no Gmail via IMAP
- verifica emails não lidos
- extrai assunto e conteúdo
- envia mensagem automaticamente para o Telegram
- ideal para rodar em servidor ou via agendador de tarefas

Requisitos

Python 3.9 ou superior

Instalação

1. Clonar o repositório

git clone https://github.com/SEU_USUARIO/boletim_automatico.git

cd boletim_automatico

2. Instalar dependências

pip install -r requirements.txt

3. Criar arquivo .env

Crie um arquivo chamado .env na pasta do projeto.

Exemplo de conteúdo:

EMAIL_GMAIL=seuemail@gmail.com
SENHA_GMAIL=sua_senha_de_app

TELEGRAM_TOKEN=seu_token_do_bot
TELEGRAM_CHAT_ID=seu_chat_id

Como obter o token do bot

No Telegram procure por BotFather.

Execute:

/newbot

Siga as instruções para criar o bot.

O bot deste projeto é:

@boletim_automatico_bot

Como obter o chat id

No Telegram procure:

userinfobot

Digite:

/start

Ele retornará seu chat id.

Execução

Rodar o script:

python boletim_automatico.py

Funcionamento

O script:

1 conecta no Gmail
2 verifica emails não lidos
3 extrai assunto e conteúdo
4 envia a mensagem para o Telegram
5 encerra execução

Uso em servidor

O script pode ser executado via agendador.

Linux (cron)

Exemplo rodando a cada 5 minutos:

_/5 _ \* \* \* python /caminho/boletim_automatico.py

Windows

Usar Agendador de Tarefas para executar o script no horário desejado.

Segurança

Nunca publique:

.env
tokens do Telegram
senha de app do Gmail

O arquivo .gitignore já bloqueia o envio do .env.

Licença

Projeto livre para uso e modificação.
