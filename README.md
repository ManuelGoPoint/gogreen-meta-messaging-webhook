# GoGreen Meta Messaging Webhook

Servicio exclusivo de GoGreen para recibir eventos oficiales de Messenger y WhatsApp, validar la firma `X-Hub-Signature-256`, normalizar mensajes y escribirlos en Google Sheets.

## Endpoints

- `GET /healthz`: estado sin exponer secretos.
- `GET /webhook/meta`: verificación de suscripción de Meta.
- `POST /webhook/meta`: recepción firmada de eventos.

## Variables de entorno

- `META_VERIFY_TOKEN`: token aleatorio compartido con la configuración del webhook de Meta.
- `META_APP_SECRET`: App Secret de la aplicación de Meta; se usa para validar HMAC SHA-256.
- `MATON_API_KEY`: credencial privada del gateway interno.
- `SHEET_ID`: ID del spreadsheet de GoGreen.
- `SHEET_TAB`: `LEADS RAW - MESSAGING`.

Nunca guardar secretos en Git. En Render se configuran como variables privadas.

## Compatibilidad

Normaliza:

- WhatsApp Cloud API: nombre de perfil, `wa_id`/teléfono, mensaje, tipo, referencia de anuncio y `message_id`.
- Messenger Platform: PSID, mensaje, tipo, referencia/anuncio y `message_id`. Messenger no entrega teléfono automáticamente.

La escritura es append-only con deduplicación por `message_id` contra la columna J de la pestaña RAW.

## Desarrollo

```bash
python -m unittest discover -s tests -v
gunicorn --bind 127.0.0.1:8000 app:app
```

## Despliegue Render

Servicio activo: `https://gogreen-meta-messaging-webhook.onrender.com`

- Runtime: Python
- Build: `pip install -r requirements.txt`
- Start: `gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 60 app:app`
- Health check: `/healthz`

La activación final de Messenger/WhatsApp está documentada en [`docs/META_SETUP.md`](docs/META_SETUP.md).
