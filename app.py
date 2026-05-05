import asyncio
import contextlib
import importlib
import importlib.metadata
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("conference-mvp")

APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"

def load_local_env() -> None:
    env_path = APP_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


load_local_env()

TRANSCRIBE_MODEL = os.environ.get(
    "MISTRAL_TRANSCRIBE_MODEL",
    "voxtral-mini-transcribe-realtime-2602",
)
TRANSLATE_MODEL = os.environ.get("MISTRAL_TRANSLATE_MODEL", "mistral-small-latest")
TARGET_DELAY_MS = int(os.environ.get("MISTRAL_TARGET_DELAY_MS", "900"))
TARGET_SAMPLE_RATE = int(os.environ.get("MISTRAL_SAMPLE_RATE", "16000"))
TRANSLATION_IDLE_FLUSH_MS = int(os.environ.get("TRANSLATION_IDLE_FLUSH_MS", "550"))
TRANSLATION_MIN_CHARS = int(os.environ.get("TRANSLATION_MIN_CHARS", "4"))
API_KEY = os.environ.get("MISTRAL_API_KEY", "")

SENTENCE_RE = re.compile(r"(.+?[.!?])(?:\s+|$)", re.S)

AUTO_MODE = "auto"
AUTO_MODE_LABEL = "Détection automatique active"
LANGUAGE_LABELS = {
    "en": "anglais",
    "fr": "français",
}
TARGET_LANGUAGE_BY_SOURCE = {
    "en": "fr",
    "fr": "en",
}
LANGUAGE_CODE_ALIASES = {
    "en": "en",
    "english": "en",
    "anglais": "en",
    "fr": "fr",
    "french": "fr",
    "francais": "fr",
    "français": "fr",
}
ENGLISH_HINT_WORDS = {
    "a",
    "and",
    "are",
    "for",
    "hello",
    "i",
    "in",
    "is",
    "it",
    "of",
    "okay",
    "on",
    "please",
    "question",
    "thank",
    "thanks",
    "the",
    "this",
    "to",
    "we",
    "what",
    "yes",
    "you",
}
FRENCH_HINT_WORDS = {
    "bonjour",
    "cette",
    "de",
    "des",
    "du",
    "est",
    "et",
    "je",
    "la",
    "le",
    "les",
    "merci",
    "nous",
    "oui",
    "pour",
    "question",
    "sur",
    "une",
    "vous",
}


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def trim_history(items: List[str], keep: int = 6) -> List[str]:
    if len(items) <= keep:
        return items
    return items[-keep:]


def extract_sentences(buffer: str) -> Tuple[List[str], str]:
    sentences: List[str] = []
    cursor = 0
    for match in SENTENCE_RE.finditer(buffer):
        sentence = compact_text(match.group(1))
        if sentence:
            sentences.append(sentence)
        cursor = match.end()
    return sentences, buffer[cursor:]


def response_text(content) -> str:
    if isinstance(content, str):
        return compact_text(content)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                text = getattr(item, "text", "")
                if text:
                    parts.append(text)
        return compact_text(" ".join(parts))
    return compact_text(str(content))


def normalize_language_code(value: Optional[str]) -> Optional[str]:
    candidate = compact_text(value or "").lower()
    if not candidate:
        return None
    direct = LANGUAGE_CODE_ALIASES.get(candidate)
    if direct:
        return direct
    if candidate.startswith("en") or "english" in candidate or "anglais" in candidate:
        return "en"
    if candidate.startswith("fr") or "french" in candidate or "français" in candidate or "francais" in candidate:
        return "fr"
    return None


def language_label(code: Optional[str]) -> str:
    normalized = normalize_language_code(code)
    if not normalized:
        return "inconnue"
    return LANGUAGE_LABELS[normalized]


def target_language_for(source_language: str) -> str:
    return TARGET_LANGUAGE_BY_SOURCE.get(source_language, "fr")


def direction_label(source_language: Optional[str], target_language: Optional[str]) -> str:
    source = normalize_language_code(source_language)
    target = normalize_language_code(target_language)
    if not source or not target:
        return "Direction: en attente"
    return f"Direction: {language_label(source)} -> {language_label(target)}"


def guess_language_heuristic(text: str) -> Optional[str]:
    normalized = compact_text(text).lower()
    if not normalized:
        return None
    if re.search(r"[àâçéèêëîïôûùüÿœ]", normalized):
        return "fr"

    tokens = set(re.findall(r"[a-zA-ZÀ-ÿ']+", normalized))
    english_score = len(tokens & ENGLISH_HINT_WORDS)
    french_score = len(tokens & FRENCH_HINT_WORDS)

    if english_score > french_score:
        return "en"
    if french_score > english_score:
        return "fr"
    return None


def parse_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    candidate = raw_text.strip()
    if not candidate:
        return None
    for possible in [candidate]:
        try:
            loaded = json.loads(possible)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded

    match = re.search(r"\{.*\}", candidate, re.S)
    if not match:
        return None
    try:
        loaded = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if isinstance(loaded, dict):
        return loaded
    return None


def get_mistral_client_class():
    try:
        return importlib.import_module("mistralai.client").Mistral
    except Exception:
        return importlib.import_module("mistralai").Mistral


def build_mistral_client(api_key: str):
    return get_mistral_client_class()(api_key=api_key)


def runtime_support_error() -> Optional[str]:
    if sys.version_info < (3, 10):
        return (
            "Python 3.10+ requis pour le mode realtime Mistral. "
            f"Version détectée: {sys.version.split()[0]}."
        )

    try:
        version = importlib.metadata.version("mistralai")
    except importlib.metadata.PackageNotFoundError:
        return "Le package `mistralai` n'est pas installé dans cet environnement."

    try:
        client = build_mistral_client("test")
    except Exception as exc:
        return f"Impossible d'initialiser le SDK Mistral: {exc}"

    try:
        importlib.import_module("mistralai.extra.realtime")
    except Exception:
        return (
            "Le SDK installé n'expose pas `mistralai.extra.realtime`. "
            f"Version détectée: {version}. Réinstallez avec `mistralai[realtime]`."
        )

    if not hasattr(client, "audio") or not hasattr(client.audio, "realtime"):
        return (
            "Le SDK Mistral installé n'expose pas `client.audio.realtime`. "
            f"Version détectée: {version}. Réinstallez avec `mistralai[realtime]`."
        )

    return None


def load_realtime_sdk():
    try:
        models = importlib.import_module("mistralai.client.models")
    except Exception:
        models = importlib.import_module("mistralai.models")
    realtime = importlib.import_module("mistralai.extra.realtime")
    return {
        "AudioFormat": getattr(models, "AudioFormat"),
        "RealtimeTranscriptionError": getattr(models, "RealtimeTranscriptionError"),
        "RealtimeTranscriptionSessionCreated": getattr(
            models, "RealtimeTranscriptionSessionCreated"
        ),
        "TranscriptionStreamDone": getattr(models, "TranscriptionStreamDone"),
        "TranscriptionStreamTextDelta": getattr(
            models, "TranscriptionStreamTextDelta"
        ),
        "UnknownRealtimeEvent": getattr(realtime, "UnknownRealtimeEvent"),
    }


def translate_between_languages(
    client,
    model: str,
    source_language: str,
    target_language: str,
    text: str,
) -> str:
    response = client.chat.complete(
        model=model,
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    "You translate live conference captions. "
                    "Keep the translation natural, concise, and readable on screen. "
                    "Preserve names, numbers, and terminology. "
                    "Return translation only, no quotes, no commentary."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Translate from {language_label(source_language)} to "
                    f"{language_label(target_language)}:\n\n{text}"
                ),
            },
        ],
    )
    return response_text(response.choices[0].message.content) or compact_text(text)


@dataclass
class TranslationResult:
    source_language: str
    target_language: str
    translated_text: str


def detect_and_translate_text(
    api_key: str,
    model: str,
    text: str,
    previous_source_language: Optional[str] = None,
) -> TranslationResult:
    client = build_mistral_client(api_key)
    normalized_previous_language = normalize_language_code(previous_source_language)
    response = client.chat.complete(
        model=model,
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    "You classify each live conference caption as English or French, "
                    "then translate it into the opposite language. "
                    "Return strict JSON only with keys "
                    "`source_language`, `target_language`, and `translated_text`. "
                    "Allowed language codes are `en` and `fr`. "
                    "If the text is short or ambiguous, prefer the previous source "
                    "language when it is provided. Keep the translation natural, "
                    "concise, and readable on screen."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "text": text,
                        "previous_source_language": normalized_previous_language,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )

    raw = response_text(response.choices[0].message.content)
    payload = parse_json_object(raw) or {}
    source_language = normalize_language_code(payload.get("source_language"))
    if not source_language:
        source_language = (
            normalized_previous_language
            or guess_language_heuristic(text)
            or "en"
        )

    target_language = normalize_language_code(payload.get("target_language"))
    expected_target_language = target_language_for(source_language)
    if target_language != expected_target_language:
        target_language = expected_target_language

    translated_value = payload.get("translated_text", "")
    translated_text = compact_text(
        translated_value if isinstance(translated_value, str) else str(translated_value or "")
    )
    if not translated_text and raw and not payload:
        translated_text = raw
    if not translated_text:
        translated_text = translate_between_languages(
            client,
            model,
            source_language,
            target_language,
            text,
        )

    return TranslationResult(
        source_language=source_language,
        target_language=target_language,
        translated_text=translated_text or compact_text(text),
    )


@dataclass
class SessionState:
    session_id: str = "default"
    mode: str = AUTO_MODE
    running: bool = False
    source_segments: List[str] = field(default_factory=list)
    translated_segments: List[str] = field(default_factory=list)
    pending_fragment: str = ""
    live_source: str = ""
    last_text_at: float = field(default_factory=time.monotonic)
    status: str = "Prêt"
    sample_rate: int = TARGET_SAMPLE_RATE
    audio_chunks_received: int = 0
    audio_bytes_received: int = 0
    detected_source_language: Optional[str] = None
    detected_target_language: Optional[str] = None


class SharedCaptionRoom:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.state = SessionState(session_id=session_id)
        self.clients: Dict[WebSocket, str] = {}
        self.send_lock = asyncio.Lock()
        self.audio_queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        self.translation_queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self.stop_event = asyncio.Event()
        self.transcribe_task: Optional[asyncio.Task] = None
        self.translate_task: Optional[asyncio.Task] = None
        self.flush_task: Optional[asyncio.Task] = None

    def refresh_live_source(self) -> None:
        self.state.live_source = compact_text(self.state.pending_fragment)

    def control_client_count(self) -> int:
        return sum(1 for role in self.clients.values() if role == "control")

    def viewer_client_count(self) -> int:
        return sum(1 for role in self.clients.values() if role == "display")

    async def run_client(self, websocket: WebSocket, role: str) -> None:
        await websocket.accept()
        self.clients[websocket] = role
        await self.push_state(target=websocket)
        try:
            while True:
                message = await websocket.receive()
                message_type = message["type"]
                if message_type == "websocket.disconnect":
                    break
                if role != "control":
                    continue
                if "text" in message and message["text"] is not None:
                    await self.handle_text(message["text"])
                elif "bytes" in message and message["bytes"] is not None:
                    await self.handle_audio(message["bytes"])
        finally:
            self.clients.pop(websocket, None)
            if self.control_client_count() == 0 and self.state.running:
                await self.stop_pipeline()
                await self.push_state()
            if not self.clients and not self.state.running:
                ROOM_MANAGER.remove_if_idle(self.session_id, self)

    async def handle_text(self, payload: str) -> None:
        data = json.loads(payload)
        action = data.get("action")
        if action == "start":
            self.state.mode = AUTO_MODE
            self.state.sample_rate = int(data.get("sampleRate", TARGET_SAMPLE_RATE))
            await self.start_pipeline()
        elif action == "stop":
            await self.stop_pipeline()
            await self.push_state()
        elif action == "clear":
            self.state.source_segments.clear()
            self.state.translated_segments.clear()
            self.state.pending_fragment = ""
            self.state.live_source = ""
            self.state.status = "Prêt"
            self.state.audio_chunks_received = 0
            self.state.audio_bytes_received = 0
            self.state.detected_source_language = None
            self.state.detected_target_language = None
            await self.push_state()
        elif action == "mode":
            self.state.mode = AUTO_MODE
            await self.push_state()

    async def handle_audio(self, chunk: bytes) -> None:
        if not self.state.running:
            return
        self.state.audio_chunks_received += 1
        self.state.audio_bytes_received += len(chunk)
        if self.state.audio_chunks_received == 1:
            logger.info(
                "Session %s first audio chunk received: %s bytes at %s Hz",
                self.session_id,
                len(chunk),
                self.state.sample_rate,
            )
        try:
            self.audio_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                _ = self.audio_queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.audio_queue.put_nowait(chunk)
        if self.state.audio_chunks_received in {1, 10} or self.state.audio_chunks_received % 50 == 0:
            await self.push_state()

    async def start_pipeline(self) -> None:
        if not API_KEY:
            self.state.status = "Clé Mistral manquante"
            await self.push_error("Définissez MISTRAL_API_KEY avant de démarrer.")
            return
        support_error = runtime_support_error()
        if support_error:
            self.state.status = "Environnement incompatible"
            await self.push_error(support_error)
            await self.push_state()
            return
        if self.state.running:
            return
        self.stop_event = asyncio.Event()
        self.audio_queue = asyncio.Queue(maxsize=128)
        self.translation_queue = asyncio.Queue(maxsize=32)
        self.state.running = True
        self.state.status = "Connexion à Mistral..."
        self.state.audio_chunks_received = 0
        self.state.audio_bytes_received = 0
        self.state.detected_source_language = None
        self.state.detected_target_language = None
        logger.info(
            "Starting realtime transcription for session=%s model=%s sample_rate=%s delay_ms=%s mode=%s",
            self.session_id,
            TRANSCRIBE_MODEL,
            self.state.sample_rate,
            TARGET_DELAY_MS,
            self.state.mode,
        )
        await self.push_state()
        self.transcribe_task = asyncio.create_task(self.transcribe_loop())
        self.translate_task = asyncio.create_task(self.translation_loop())
        self.flush_task = asyncio.create_task(self.flush_loop())

    async def stop_pipeline(self) -> None:
        self.state.running = False
        self.stop_event.set()
        await self.queue_sentinel(self.audio_queue, None)
        await self.queue_sentinel(self.translation_queue, None)
        for task in [self.transcribe_task, self.translate_task, self.flush_task]:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.transcribe_task = None
        self.translate_task = None
        self.flush_task = None
        if self.state.status != "Clé Mistral manquante":
            self.state.status = "Arrêté"

    async def queue_sentinel(self, queue: asyncio.Queue, value) -> None:
        try:
            queue.put_nowait(value)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                _ = queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(value)

    async def audio_stream(self):
        while True:
            chunk = await self.audio_queue.get()
            if chunk is None:
                break
            yield chunk

    async def transcribe_loop(self) -> None:
        sdk = load_realtime_sdk()
        client = build_mistral_client(API_KEY)
        audio_format = sdk["AudioFormat"](
            encoding="pcm_s16le",
            sample_rate=self.state.sample_rate,
        )
        try:
            async for event in client.audio.realtime.transcribe_stream(
                audio_stream=self.audio_stream(),
                model=TRANSCRIBE_MODEL,
                audio_format=audio_format,
                target_streaming_delay_ms=TARGET_DELAY_MS,
            ):
                if isinstance(event, sdk["RealtimeTranscriptionSessionCreated"]):
                    logger.info(
                        "Realtime transcription session created for room %s.",
                        self.session_id,
                    )
                    self.state.status = "Écoute en direct"
                    await self.push_state()
                elif isinstance(event, sdk["TranscriptionStreamTextDelta"]):
                    logger.info(
                        "Session %s transcript delta received: %r",
                        self.session_id,
                        event.text[:80],
                    )
                    await self.consume_delta(event.text)
                elif isinstance(event, sdk["TranscriptionStreamDone"]):
                    logger.info("Realtime transcription stream done for room %s.", self.session_id)
                    self.state.status = "Flux terminé"
                    await self.push_state()
                elif isinstance(event, sdk["RealtimeTranscriptionError"]):
                    logger.error("Realtime transcription error on room %s: %s", self.session_id, event)
                    self.state.status = "Erreur de transcription"
                    await self.push_error(str(event))
                elif isinstance(event, sdk["UnknownRealtimeEvent"]):
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state.status = "Erreur de connexion"
            await self.push_error(str(exc))
            await self.push_state()

    async def consume_delta(self, delta: str) -> None:
        if not delta:
            return
        self.state.pending_fragment += delta
        self.state.last_text_at = time.monotonic()
        completed, rest = extract_sentences(self.state.pending_fragment)
        self.state.pending_fragment = rest
        for sentence in completed:
            await self.enqueue_segment(sentence)
        self.refresh_live_source()
        await self.push_state()

    async def enqueue_segment(self, sentence: str) -> None:
        clean = compact_text(sentence)
        if not clean:
            return
        self.state.source_segments.append(clean)
        self.state.source_segments = trim_history(self.state.source_segments)
        try:
            self.translation_queue.put_nowait(clean)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                _ = self.translation_queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.translation_queue.put_nowait(clean)

    async def flush_loop(self) -> None:
        try:
            while not self.stop_event.is_set():
                await asyncio.sleep(0.35)
                idle_for = time.monotonic() - self.state.last_text_at
                stale_fragment = compact_text(self.state.pending_fragment)
                if (
                    stale_fragment
                    and idle_for >= (TRANSLATION_IDLE_FLUSH_MS / 1000)
                    and len(stale_fragment) >= TRANSLATION_MIN_CHARS
                ):
                    self.state.pending_fragment = ""
                    await self.enqueue_segment(stale_fragment)
                    self.refresh_live_source()
                    await self.push_state()
        except asyncio.CancelledError:
            raise

    async def translation_loop(self) -> None:
        try:
            while True:
                item = await self.translation_queue.get()
                if item is None:
                    break
                sentence = item
                result = await asyncio.to_thread(
                    detect_and_translate_text,
                    API_KEY,
                    TRANSLATE_MODEL,
                    sentence,
                    self.state.detected_source_language,
                )
                self.state.detected_source_language = result.source_language
                self.state.detected_target_language = result.target_language
                self.state.translated_segments.append(result.translated_text)
                self.state.translated_segments = trim_history(
                    self.state.translated_segments
                )
                await self.push_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.push_error(str(exc))
            await self.push_state()

    async def send_json(self, websocket: WebSocket, payload: dict) -> bool:
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            return False

    async def push_error(
        self, message: str, target: Optional[WebSocket] = None
    ) -> None:
        payload = {"type": "error", "message": message, "sessionId": self.session_id}
        if target is not None:
            async with self.send_lock:
                await self.send_json(target, payload)
            return
        async with self.send_lock:
            broken = []
            for websocket in list(self.clients.keys()):
                ok = await self.send_json(websocket, payload)
                if not ok:
                    broken.append(websocket)
            for websocket in broken:
                self.clients.pop(websocket, None)

    async def push_state(self, target: Optional[WebSocket] = None) -> None:
        payload = {
            "type": "state",
            "sessionId": self.session_id,
            "mode": self.state.mode,
            "modeLabel": AUTO_MODE_LABEL,
            "directionLabel": direction_label(
                self.state.detected_source_language,
                self.state.detected_target_language,
            ),
            "detectedSourceLanguage": self.state.detected_source_language,
            "detectedTargetLanguage": self.state.detected_target_language,
            "running": self.state.running,
            "status": self.state.status,
            "sourceLines": trim_history(self.state.source_segments, keep=3),
            "translationLines": trim_history(self.state.translated_segments, keep=3),
            "liveSource": self.state.live_source,
            "sampleRate": self.state.sample_rate,
            "audioChunksReceived": self.state.audio_chunks_received,
            "audioBytesReceived": self.state.audio_bytes_received,
            "controlClientCount": self.control_client_count(),
            "viewerClientCount": self.viewer_client_count(),
        }
        if target is not None:
            async with self.send_lock:
                await self.send_json(target, payload)
            return
        async with self.send_lock:
            broken = []
            for websocket in list(self.clients.keys()):
                ok = await self.send_json(websocket, payload)
                if not ok:
                    broken.append(websocket)
            for websocket in broken:
                self.clients.pop(websocket, None)


class RoomManager:
    def __init__(self):
        self.rooms: Dict[str, SharedCaptionRoom] = {}

    def get(self, session_id: str) -> SharedCaptionRoom:
        normalized = normalize_session_id(session_id)
        room = self.rooms.get(normalized)
        if room is None:
            room = SharedCaptionRoom(normalized)
            self.rooms[normalized] = room
        return room

    def remove_if_idle(self, session_id: str, room: SharedCaptionRoom) -> None:
        current = self.rooms.get(session_id)
        if current is room and not room.clients and not room.state.running:
            self.rooms.pop(session_id, None)


def normalize_session_id(value: Optional[str]) -> str:
    candidate = compact_text(value or "default").lower()
    candidate = re.sub(r"[^a-z0-9_-]+", "-", candidate)
    candidate = candidate.strip("-_")
    return candidate or "default"


ROOM_MANAGER = RoomManager()


app = FastAPI(title="Conference Translation MVP")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/control/{session_id}")
async def control_page(session_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/display/{session_id}")
async def display_page(session_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    role = websocket.query_params.get("role", "control")
    role = "display" if role == "display" else "control"
    room = ROOM_MANAGER.get(session_id)
    await room.run_client(websocket, role)
