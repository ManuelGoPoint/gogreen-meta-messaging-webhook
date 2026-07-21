# Activación en Meta Developers — GoGreen

## Recursos ya operativos

- Callback: `https://gogreen-meta-messaging-webhook.onrender.com/webhook/meta`
- Salud: `https://gogreen-meta-messaging-webhook.onrender.com/healthz`
- Página Facebook Go Green APU: `544889629006881`
- Spreadsheet RAW: `1vtDtaAoyIwN2sarAsCgNpB8PsknLUfWYIfOr3-ZGnY4`
- Pestaña: `LEADS RAW - MESSAGING`

## Prerrequisito pendiente

Se necesita una sesión o usuario con rol **Administrador/Developer de la Meta App** asociada a Messenger y/o WhatsApp. El acceso de Ads/Page disponible no permite administrar callbacks de Meta Developers.

No enviar el App Secret por chat. Debe colocarse directamente como variable privada `META_APP_SECRET` en Render.

## Activación de Messenger

1. Abrir Meta Developers y seleccionar la app correcta.
2. Agregar/configurar el producto **Messenger**.
3. En Webhooks, usar el callback indicado arriba.
4. Usar el valor privado de `META_VERIFY_TOKEN` guardado en Render; nunca documentarlo en Git.
5. Suscribir los campos mínimos:
   - `messages`
   - `messaging_postbacks`
   - `messaging_referrals`
6. Suscribir la app a la página `544889629006881`.
7. Enviar un mensaje real desde otra cuenta y verificar una fila con `channel=messenger`.

## Activación de WhatsApp Cloud API

1. En la misma app o en la app operativa del WABA, abrir **WhatsApp > Configuration**.
2. Reemplazar en Render `META_APP_SECRET` por el App Secret real de esa app.
3. Registrar el callback indicado arriba y el Verify Token privado.
4. Suscribir el campo `messages` del WABA.
5. Enviar un mensaje real al número de WhatsApp Business y verificar una fila con `channel=whatsapp`.

## Pruebas de aceptación

- `GET /healthz` devuelve `200` y no expone secretos.
- Verificación correcta devuelve exactamente el challenge de Meta.
- Verificación incorrecta devuelve `403`.
- POST sin `X-Hub-Signature-256` válido devuelve `403`.
- Primer mensaje firmado se inserta una vez.
- Reintento con el mismo `message_id` no crea otra fila.
- Messenger puede llegar sin teléfono; queda marcado `Nuevo lead — datos incompletos`.
- WhatsApp normaliza `wa_id` como teléfono con prefijo `+`.

## Seguridad

- Nunca guardar `META_APP_SECRET`, `META_VERIFY_TOKEN` ni `MATON_API_KEY` en Git.
- Mantener validación HMAC SHA-256 obligatoria.
- El servicio solo guarda mensajes entrantes; ignora ecos salientes de Messenger.
- Los payloads crudos se guardan para auditoría con hash SHA-256 y límite de tamaño por celda.
