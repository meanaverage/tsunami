FROM node:22-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        python3 \
        python3-pip \
        python3-venv \
        git \
        ripgrep \
        procps \
        psmisc \
        lsof \
        file \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages playwright==1.58.0 \
    && python3 -m playwright install --with-deps chromium

WORKDIR /workspace/tsunami

COPY requirements.lock ./requirements.lock
RUN python3 -m pip install --break-system-packages -r requirements.lock

COPY . .

CMD ["sleep", "infinity"]
