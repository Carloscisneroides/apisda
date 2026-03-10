FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tor \
 && rm -rf /var/lib/apt/lists/*

# Tor: habilitar ControlPort con contraseña vacía
RUN printf "ControlPort 9051\nCookieAuthentication 0\nSocksPort 9050\nFascistFirewall 1\n" \
    >> /etc/tor/torrc

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8080

CMD ["./start.sh"]
