"""Demo de Gemini 3.1 Flash Live con micro/altavoz local + tools de Sprintia.

Sustituto local de ElevenLabs Conversational AI para A/B test:
- Captura audio del micro a 16kHz PCM16 mono y lo envía a Gemini en tiempo real.
- Recibe audio a 24kHz PCM16 mono y lo reproduce por el altavoz.
- Detecta interrupciones del cliente (barge-in) y vacía la cola de reproducción.
- Resuelve function calls síncronas contra el backend de Railway (mismas tools
  que Ana usa en producción con ElevenLabs).
- Imprime transcripciones y eventos en consola para debug.

Ejecución:
    cd demo_gemini_live
    pip install -r requirements.txt
    python gemini_live_demo.py

Ctrl+C para colgar.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
import traceback
from typing import Any

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types

from config import settings
from prompt import render_prompt
from tools_adapter import (
    TOOL_DECLARATIONS,
    close_http_client,
    handle_function_call,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("demo")
# Bajamos el ruido de google-genai (logging interno verboso al abrir WebSocket).
logging.getLogger("google_genai").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Audio: utilidades del micro y el altavoz.
# ---------------------------------------------------------------------------

def _make_input_stream(audio_in_q: asyncio.Queue[bytes], loop: asyncio.AbstractEventLoop):
    """Crea el stream de captura del micro. Cada chunk va a una asyncio.Queue.

    sounddevice corre en su propio hilo (PortAudio). Para enviar los chunks
    a la coroutine de envío sin race conditions, usamos run_coroutine_threadsafe
    contra el event loop principal.
    """
    chunk = settings.audio_chunk_samples_in

    def callback(indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            # Underflow/overflow del driver — ruidoso pero no fatal.
            log.debug("input stream status: %s", status)
        # indata es float32 [-1,1] mono shape (frames, 1). Convertimos a PCM16 LE.
        pcm16 = (indata[:, 0] * 32767.0).astype(np.int16).tobytes()
        # Encola sin bloquear el hilo de audio.
        try:
            asyncio.run_coroutine_threadsafe(audio_in_q.put(pcm16), loop)
        except RuntimeError:
            # Loop cerrado, ignorar.
            pass

    return sd.InputStream(
        samplerate=settings.audio_input_sr,
        blocksize=chunk,
        channels=1,
        dtype="float32",
        callback=callback,
    )


def _make_output_stream():
    """Crea el stream de reproducción del altavoz.

    sounddevice.RawOutputStream a 24kHz mono PCM16. Lo escribiremos en bloques
    desde la coroutine consumidora de la cola de audio out.
    """
    return sd.RawOutputStream(
        samplerate=settings.audio_output_sr,
        channels=1,
        dtype="int16",
        # blocksize=0 deja que PortAudio elija el óptimo del driver.
        blocksize=0,
    )


# ---------------------------------------------------------------------------
# Tasks principales de la sesión Live.
# ---------------------------------------------------------------------------

class GeminiLiveSession:
    """Encapsula una sesión Live y orquesta sus tres tasks (mic, recv, speaker)."""

    def __init__(self, session: Any, audio_out_q: asyncio.Queue[bytes]) -> None:
        self.session = session
        self.audio_out_q = audio_out_q
        # Cuando el usuario interrumpe al modelo, vaciamos la cola para cortar
        # la reproducción a medias. También se vacía al recibir end_call.
        self.stop_speaker = asyncio.Event()
        self.shutdown = asyncio.Event()

    # --- Mic → sesión ----------------------------------------------------
    async def mic_to_session(self, audio_in_q: asyncio.Queue[bytes]) -> None:
        try:
            mime = f"audio/pcm;rate={settings.audio_input_sr}"
            while not self.shutdown.is_set():
                chunk = await audio_in_q.get()
                if not chunk:
                    continue
                await self.session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type=mime),
                )
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.exception("mic_to_session caído")
            self.shutdown.set()

    # --- Sesión → cola de altavoz + tool calls --------------------------
    async def recv_loop(self) -> None:
        try:
            async for response in self.session.receive():
                if self.shutdown.is_set():
                    return

                # Audio del modelo (puede llegar partido en varios eventos).
                if response.data:
                    await self.audio_out_q.put(response.data)

                # Texto (transcripción para debug, si la activamos en config).
                if response.text:
                    print(f"\n[ANA] {response.text}", flush=True)

                # Server content: turnos del modelo, transcripciones de input,
                # interrupciones.
                sc = getattr(response, "server_content", None)
                if sc is not None:
                    if getattr(sc, "interrupted", False):
                        log.info("⚡ usuario interrumpió — vaciando audio out")
                        await self._drain_speaker()
                    # Transcripción del input del usuario (si está activada).
                    inp_tr = getattr(sc, "input_transcription", None)
                    if inp_tr and getattr(inp_tr, "text", None):
                        print(f"[USR] {inp_tr.text}", flush=True)
                    out_tr = getattr(sc, "output_transcription", None)
                    if out_tr and getattr(out_tr, "text", None):
                        print(f"[ANA-tr] {out_tr.text}", flush=True)

                # Tool calls (síncronas: bloquean al modelo hasta que respondamos).
                tc = getattr(response, "tool_call", None)
                if tc and getattr(tc, "function_calls", None):
                    await self._handle_tool_calls(tc.function_calls)

                # Cancelación de tool calls pendientes (típicamente tras barge-in).
                tcc = getattr(response, "tool_call_cancellation", None)
                if tcc and getattr(tcc, "ids", None):
                    log.info("tool calls canceladas: %s", tcc.ids)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.exception("recv_loop caído")
            self.shutdown.set()

    # --- Cola de altavoz → sounddevice -----------------------------------
    async def speaker_consumer(self, output_stream) -> None:
        try:
            while not self.shutdown.is_set():
                try:
                    chunk = await asyncio.wait_for(self.audio_out_q.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if not chunk:
                    continue
                # write() de sounddevice es bloqueante; lo hacemos en threadpool
                # para no bloquear el event loop.
                await asyncio.get_running_loop().run_in_executor(
                    None, output_stream.write, chunk,
                )
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.exception("speaker_consumer caído")
            self.shutdown.set()

    # --- Helpers internos ------------------------------------------------
    async def _drain_speaker(self) -> None:
        """Vacía la cola de audio out (barge-in)."""
        drained = 0
        while not self.audio_out_q.empty():
            try:
                self.audio_out_q.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            log.debug("drenados %d chunks de audio out", drained)

    async def _handle_tool_calls(self, function_calls: list) -> None:
        """Resuelve tool calls en paralelo y devuelve las respuestas a la sesión.

        Gemini 3.1 Flash Live solo soporta function calling síncrono: el modelo
        no continúa hasta recibir send_tool_response. Por eso ejecutamos las
        tools en paralelo (asyncio.gather) — si Ana llama a varias seguidas, no
        las serializamos artificialmente.
        """
        async def _one(fc):
            name = fc.name
            args = dict(fc.args or {})
            print(f"[TOOL→] {name}({_short_repr(args)})", flush=True)
            try:
                result = await handle_function_call(name, args)
            except Exception as e:  # noqa: BLE001
                log.exception("tool %s reventó", name)
                result = {"ok": False, "error": "Excepción en tool", "detail": str(e)[:200]}
            print(f"[TOOL←] {name} → {_short_repr(result)}", flush=True)

            # end_call: tras devolver la respuesta, marcamos shutdown para que
            # el loop principal cierre la sesión y el programa.
            if name == "end_call":
                # Pequeño delay para que el modelo procese la respuesta antes
                # de que cerremos el WebSocket.
                async def _close_later():
                    await asyncio.sleep(2.0)
                    log.info("end_call recibido → cerrando sesión")
                    self.shutdown.set()
                asyncio.create_task(_close_later())

            return types.FunctionResponse(
                id=fc.id,
                name=name,
                response=result if isinstance(result, dict) else {"result": result},
            )

        responses = await asyncio.gather(*[_one(fc) for fc in function_calls])
        await self.session.send_tool_response(function_responses=responses)


def _short_repr(obj: Any, n: int = 200) -> str:
    s = repr(obj)
    return s if len(s) <= n else s[: n - 3] + "..."


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

def _build_config() -> dict[str, Any]:
    """Construye el LiveConnectConfig: prompt + tools + voz + transcripciones."""
    system_instruction = render_prompt()
    log.info(
        "prompt cargado: %d chars, voz=%s, modelo=%s",
        len(system_instruction), settings.gemini_voice, settings.gemini_model,
    )

    return {
        # AUDIO en lugar de TEXT — queremos voz, no texto.
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": settings.gemini_voice},
            },
        },
        "tools": [{"function_declarations": TOOL_DECLARATIONS}],
        # Transcripción de la voz del usuario y de la voz de Ana — útil para
        # debug y para los logs de sesión. No afecta a la latencia perceptible.
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }


async def _run_session() -> None:
    client = genai.Client(api_key=settings.gemini_api_key)
    config = _build_config()

    audio_in_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
    audio_out_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=400)
    loop = asyncio.get_running_loop()

    print("\n─────────────────────────────────────────────────────────")
    print(" Demo Gemini 3.1 Flash Live — Ana (peluquería pelu_demo)")
    print(f"   Modelo: {settings.gemini_model}")
    print(f"   Voz:    {settings.gemini_voice}")
    print(f"   Backend: {settings.backend_url} (tenant {settings.tenant_id})")
    print("─────────────────────────────────────────────────────────")
    print("Habla cuando quieras. Ctrl+C para colgar.\n")

    async with client.aio.live.connect(
        model=settings.gemini_model, config=config,
    ) as session:
        gls = GeminiLiveSession(session, audio_out_q)

        input_stream = _make_input_stream(audio_in_q, loop)
        output_stream = _make_output_stream()
        input_stream.start()
        output_stream.start()

        try:
            await asyncio.gather(
                gls.mic_to_session(audio_in_q),
                gls.recv_loop(),
                gls.speaker_consumer(output_stream),
                _wait_shutdown(gls.shutdown),
            )
        finally:
            log.info("cerrando streams de audio")
            try:
                input_stream.stop()
                input_stream.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                output_stream.stop()
                output_stream.close()
            except Exception:  # noqa: BLE001
                pass


async def _wait_shutdown(event: asyncio.Event) -> None:
    await event.wait()
    log.info("shutdown event activado")


def _install_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """Ctrl+C → marca shutdown limpio en el loop."""
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            # Windows no soporta add_signal_handler para SIGTERM.
            pass


async def main() -> None:
    try:
        await _run_session()
    except KeyboardInterrupt:
        print("\n^C — colgando…")
    except Exception:
        log.exception("error fatal en la sesión")
        traceback.print_exc()
    finally:
        await close_http_client()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbye.")
        sys.exit(0)
