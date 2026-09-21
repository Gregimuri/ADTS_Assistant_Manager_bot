FROM python:3.12-slim

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Можно переопределить зеркало: docker compose build --build-arg PIP_INDEX_URL=...
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org files.pythonhosted.org pypi.python.org

COPY requirements.txt .

RUN pip install --upgrade pip \
 && pip_hosts="$(printf -- '--trusted-host %s ' $PIP_TRUSTED_HOST)" \
 && ( \
      pip install --no-cache-dir --retries 15 $pip_hosts -i "$PIP_INDEX_URL" -r requirements.txt \
      || pip install --no-cache-dir --retries 15 \
           --trusted-host mirror.yandex.ru \
           -i https://mirror.yandex.ru/mirrors/pypi/simple/ \
           -r requirements.txt \
      || pip install --no-cache-dir --retries 15 \
           --trusted-host pypi.tuna.tsinghua.edu.cn \
           -i https://pypi.tuna.tsinghua.edu.cn/simple \
           -r requirements.txt \
    )

COPY app ./app
RUN mkdir -p /app/data && chmod 777 /app/data

CMD ["python", "-m", "app.main"]
