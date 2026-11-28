# Образ бота для сервера. Секреты в образ не попадают: их подключает compose.yaml.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала зависимости, потом код: при правке кода Docker не переустанавливает библиотеки.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY expense_bot ./expense_bot

CMD ["python", "-m", "expense_bot.bot"]
