#!/bin/sh
# Arrancar Tor en background y esperar que esté listo
tor &
echo "Esperando Tor..."
sleep 8

exec gunicorn \
    --bind 0.0.0.0:8080 \
    --workers 2 \
    --threads 4 \
    --timeout 30 \
    --graceful-timeout 20 \
    --preload \
    --access-logfile - \
    --error-logfile - \
    app:app
