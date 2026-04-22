# Configuración del agente Ana en ElevenLabs

Este repo **no aloja** el agente (vive en ElevenLabs Conversational AI). Pero
para que los ajustes no se pierdan si alguien toca la UI y la cosa se descuadra,
guardamos aquí un snapshot del estado que sabemos que funciona.

## Archivos

- `elevenlabs_agent_config.json` — snapshot completo del agente (TTS, LLM,
  turn-taking, tools inline, etc.). El `prompt` del agente se sustituye por un
  placeholder que apunta a `ana_prompt_new.txt` (que sí se versiona entero).
  Las headers `X-Tool-Secret` se sustituyen por `<<TOOL_SECRET — see .env>>`.
- `ana_prompt_new.txt` — el system prompt real que se sube al agente.

## Valores clave (última verificación)

TTS (voz más natural que la inicial):
- `voice_id`: `1eHrpOW5l98cxiSRjbzJ`
- `model_id`: `eleven_flash_v2_5`
- `stability`: `0.67`
- `similarity_boost`: `0.8`
- `speed`: `1.04`
- `optimize_streaming_latency`: `4`

LLM (Gemini Flash con thinking OFF para bajar latencia):
- `llm`: `gemini-2.5-flash`
- `thinking_budget`: `0`
- `temperature`: `0.3`
- `max_tokens`: `-1`

Turn-taking (agente responde rápido):
- `turn_eagerness`: `eager`
- `turn_timeout`: `5.0`
- `speculative_turn`: `true`

Tools asociadas (`tool_ids` en el agente):
- `consultar_disponibilidad`
- `crear_reserva` (body con `telefono_cliente`, sin query param)
- `buscar_reserva_cliente` (body con `telefono_cliente`, sin query param)
- `mover_reserva`
- `cancelar_reserva`

## Cómo regenerar este snapshot

```bash
set -a && . ./.env && set +a
curl -s "https://api.elevenlabs.io/v1/convai/agents/${ELEVENLABS_AGENT_ID}" \
  -H "xi-api-key: $ELEVENLABS_API_KEY" > /tmp/agent_raw.json
python3 - <<'PY'
import json
d = json.load(open('/tmp/agent_raw.json'))
for f in ('access_info','usage_stats','created_at_unix_secs'): d.pop(f, None)
p = ((d.get('conversation_config') or {}).get('agent') or {}).get('prompt') or {}
if p.get('prompt'): p['prompt'] = '<<see ana_prompt_new.txt>>'
for t in p.get('tools') or []:
    hdrs = (((t.get('api_schema') or {}).get('request_headers')) or {})
    if 'X-Tool-Secret' in hdrs:
        hdrs['X-Tool-Secret'] = '<<TOOL_SECRET — see .env>>'
json.dump(d, open('elevenlabs_agent_config.json','w'), ensure_ascii=False, indent=2)
PY
```

## Cómo restaurar desde snapshot (si algo se descuadra)

No hay un endpoint de "replace" — se hace con PATCH por secciones. Lo más
práctico es abrir el snapshot, coger los valores de TTS / LLM / turn y
`PATCH`-earlos a `/v1/convai/agents/{id}`. Para el prompt, se usa
`ana_prompt_new.txt`. Para las tools, cada `tool_id` se `PATCH`-ea por
separado restaurando su `api_schema` (y metiendo a mano el `X-Tool-Secret`
real desde `.env`).
