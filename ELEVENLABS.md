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
- `voice_id`: `1eHrpOW5l98cxiSRjbzJ`  (Raquel — Young, Bright and Cheerful)
- `model_id`: `eleven_v3_conversational`  ⬅ **nuevo en ronda 8 (2026-05-03)**. Sustituye a `eleven_flash_v2_5`. v3 no tiene badge "high quality base" para Raquel (ni para ninguna voz del workspace) porque sigue en alpha, pero el TTS funciona y suena bien. Bench post-migración: TTFA 1665 ms (vs 3148 ms baseline). Datos en `CHANGELOG.md` → 2026-05-03 (latencia — ronda 8).
- `stability`: `0.67`
- `similarity_boost`: `0.8`
- `speed`: `1.04`
- `optimize_streaming_latency`: `4`

LLM (Gemini Flash con thinking OFF para bajar latencia):
- `llm`: `gemini-3-flash-preview`  ⬅ confirmado en ronda 8 (2026-05-03). Antes de la ronda 8 el agente real de `pelu_demo` estaba drifteado a `gemini-3.1-pro-preview` (bug: 0/3 tool calls en bench). Restaurado al canónico en la ronda 8. Original (ronda 6, 2026-04-24): sustituye a `gemini-2.5-flash`. El bench con WS text-only midió TTFR ~1200ms vs ~4500ms del anterior (3.5x más rápido), TT_final post-tool ~3000ms vs ~7500-10400ms (2.5-3x). Tool-calling fiable en 4/4 escenarios (reserva simple, reserva con peluquero, mover, cancelar). Datos detallados en `CHANGELOG.md` → 2026-04-24 (latencia — ronda 6).
- `thinking_budget`: `0`
- `temperature`: `0.3`
- `max_tokens`: `220`  (cap más agresivo para cortar divagaciones y rascar latencia sin perder calidad)
- Prompt: comprimido a ~3.8 KB (antes 5.7 KB) para bajar prefill time cada turno.

Modelos descartados en el bench de ronda 6 (guía anti-regresión):
- `qwen3-30b-a3b` — TTFR 319ms ✓ rapidísimo pero **NO llama a tools** (0 tool calls en 8 turnos). Inútil para reservas.
- `glm-45-air-fp8` — TTFR 557ms pero **alucina**: contesta "¡Hola Juan! Te apunto un corte para mañana por la tarde" sin haber llamado a `consultar_disponibilidad`. Agenda inventada.
- `gpt-oss-120b` — TTFR 910-9134ms (muy alta varianza), experimental. Llama a tools pero tiempos inconsistentes.
- `watt-tool-70b` — TTFR ~6400ms, TT_final 12s (muy lento para voz).
- `gemini-2.5-flash-lite` — TTFR 1061ms pero no llama a tools.

Turn-taking (agente responde rápido):
- `turn_model`: `turn_v3`  ⬅ **nuevo en ronda 6**. Antes `turn_v2`.
- `turn_eagerness`: `eager`
- `turn_timeout`: `1.0`  (mínimo permitido por ElevenLabs; 5s era el default)
- `speculative_turn`: `true`

Audio formato:
- `tts.agent_output_audio_format`: `ulaw_8000`  (match Twilio nativo, evita transcode)
- `asr.user_input_audio_format`: `pcm_16000`
- `asr.quality`: `high`  (obligatorio por la API; no se puede bajar)

Tools:
- `tool_call_sound`: `typing` en las 5 tools. Suena tecleo mientras Ana consulta Google Calendar, enmascara latencia del tool audiblemente.

Primer saludo seguro:
- `first_message`: `Hola, soy Ana de la peluquería. ¿En qué te puedo ayudar?`
- Política: evitar signos de exclamación, tacos o variaciones creativas en el arranque. Si hace falta personalizarlo, hacerlo desde código / prompt, no manualmente en UI.

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
