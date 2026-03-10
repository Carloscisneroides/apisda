# ShipLisa · Poste Italiane Proxy

## Deploy en Render (paso a paso)

### 1. Subir código a GitHub
```bash
git init
git add .
git commit -m "feat: poste italiane tracking proxy"
git remote add origin https://github.com/TU_ORG/shiplisa-poste-proxy.git
git push -u origin main
```

### 2. Crear servicio en Render
1. Ir a https://dashboard.render.com → **New → Web Service**
2. Conectar el repositorio GitHub
3. Render detecta `render.yaml` automáticamente → click **Apply**
4. Esperar build (~2 min)
5. URL pública: `https://shiplisa-poste-proxy.onrender.com`

### 3. Verificar health check
```bash
curl https://shiplisa-poste-proxy.onrender.com/health
# {"status":"ok","ts":"2026-03-10T12:00:00+00:00"}
```

---

## Ejemplos cURL

### Track exitoso
```bash
curl -s -X POST https://shiplisa-poste-proxy.onrender.com/track/poste \
  -H "Content-Type: application/json" \
  -d '{"codiceSpedizione": "281807J039251"}' | jq .
```

Respuesta esperada:
```json
{
  "success": true,
  "tracking": "281807J039251",
  "carrier": "POSTE",
  "status": "DELIVERED",
  "status_text": "la spedizione è stata consegnata",
  "events": [
    {
      "timestamp": 1768447440000,
      "status": "DELIVERED",
      "location": "Bari (BA)",
      "raw_status": "la spedizione è in transito"
    }
  ]
}
```

### Track no encontrado
```bash
curl -s -X POST https://shiplisa-poste-proxy.onrender.com/track/poste \
  -H "Content-Type: application/json" \
  -d '{"codiceSpedizione": "INVALIDO"}' | jq .
# {"success":false,"error":"NOT_FOUND"}
```

### Sin parámetro
```bash
curl -s -X POST https://shiplisa-poste-proxy.onrender.com/track/poste \
  -H "Content-Type: application/json" \
  -d '{}' | jq .
# {"success":false,"error":"MISSING_TRACKING_NUMBER"}
```

---

## n8n Workflow (JSON importable)

Pegar en n8n → **Import from JSON**:

```json
{
  "name": "ShipLisa · Track Poste Italiane",
  "nodes": [
    {
      "parameters": {
        "httpMethod": "POST",
        "path": "track-poste",
        "responseMode": "responseNode"
      },
      "name": "Webhook Trigger",
      "type": "n8n-nodes-base.webhook",
      "position": [200, 300]
    },
    {
      "parameters": {
        "method": "POST",
        "url": "https://shiplisa-poste-proxy.onrender.com/track/poste",
        "sendBody": true,
        "bodyParameters": {
          "parameters": [
            {
              "name": "codiceSpedizione",
              "value": "={{ $json.body.codiceSpedizione }}"
            }
          ]
        },
        "options": {
          "timeout": 15000
        }
      },
      "name": "Call Proxy",
      "type": "n8n-nodes-base.httpRequest",
      "position": [450, 300]
    },
    {
      "parameters": {
        "conditions": {
          "boolean": [
            {
              "value1": "={{ $json.success }}",
              "value2": true
            }
          ]
        }
      },
      "name": "Success?",
      "type": "n8n-nodes-base.if",
      "position": [700, 300]
    },
    {
      "parameters": {
        "respondWith": "json",
        "responseBody": "={{ JSON.stringify($json) }}"
      },
      "name": "Respond OK",
      "type": "n8n-nodes-base.respondToWebhook",
      "position": [950, 200]
    },
    {
      "parameters": {
        "respondWith": "json",
        "responseBody": "={{ JSON.stringify({error: $json.error}) }}",
        "options": { "responseCode": 404 }
      },
      "name": "Respond Error",
      "type": "n8n-nodes-base.respondToWebhook",
      "position": [950, 400]
    }
  ],
  "connections": {
    "Webhook Trigger": { "main": [[{ "node": "Call Proxy", "type": "main", "index": 0 }]] },
    "Call Proxy":      { "main": [[{ "node": "Success?",   "type": "main", "index": 0 }]] },
    "Success?":        {
      "main": [
        [{ "node": "Respond OK",    "type": "main", "index": 0 }],
        [{ "node": "Respond Error", "type": "main", "index": 0 }]
      ]
    }
  }
}
```

### Cómo usar el workflow n8n
1. Importar JSON en n8n
2. Activar el workflow
3. n8n genera una URL webhook (ej: `https://tu-n8n.com/webhook/track-poste`)
4. ShipLisa llama a ese webhook con `{"codiceSpedizione": "281807J039251"}`
5. n8n llama al proxy y retorna respuesta normalizada

---

## Variables de entorno opcionales

| Variable | Default | Descripción |
|----------|---------|-------------|
| `PORT`   | `8080`  | Puerto HTTP |

---

## Test local con Docker
```bash
docker build -t poste-proxy .
docker run -p 8080:8080 poste-proxy

curl -X POST http://localhost:8080/track/poste \
  -H "Content-Type: application/json" \
  -d '{"codiceSpedizione": "281807J039251"}'
```
