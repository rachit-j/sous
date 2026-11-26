"""Opt-in AWS Nova 2 Sonic conversational voice (docs/voice.md).

serve() runs on the WebSocket request thread: it owns the browser socket, authentication,
limits, vault checks and session polling. VoiceBridge runs one upstream duplex stream on its own
event loop thread and exchanges data with serve() only through bounded thread-safe queues.
Guidance never mutates cooking state. Audio and transcripts are relayed, never stored or logged.
"""

import asyncio
import base64
import binascii
import collections
import hmac
import json
import logging
import queue
import re
import threading
import time
import uuid

from flask import current_app
from simple_websocket import ConnectionClosed

import cooking

MODEL_ID = "amazon.nova-2-sonic-v1:0"
SUPPORTED_REGIONS = ("us-east-1", "us-west-2", "eu-north-1", "ap-northeast-1")
VOICES = ("tiffany", "matthew")
DEFAULT_VOICE = "tiffany"
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000

MAX_AUDIO_FRAME = 16000  # bytes: 0.5 s of PCM16 mono 16 kHz
MAX_CONTROL_BYTES = 2048
MAX_SOCKET_MESSAGE = 64 * 1024  # simple-websocket closes the socket on larger messages
MAX_UPSTREAM_EVENT = 1024 * 1024
MAX_OUTPUT_AUDIO = 256 * 1024  # decoded bytes per upstream audio event
MAX_TRANSCRIPT = 2000
MAX_CONTEXT = 6000
MAX_NOTE_TEXT = 300
START_TIMEOUT = 5.0  # seconds to receive the start message
CONNECT_TIMEOUT = 15.0  # seconds to open and set up the upstream stream
MAX_STREAM_SECONDS = 420.0  # AWS closes bidirectional streams at 8 minutes; no reconnection
IDLE_TIMEOUT = 30.0  # seconds without microphone audio
SHUTDOWN_TIMEOUT = 5.0  # bridge join; covers the bounded cleanup steps below
CLOSE_STEP_TIMEOUT = 0.5
TICK = 0.02
POLL_INTERVAL = 1.0
INPUT_QUEUE_MAX = 64
OUTBOX_MAX = 256
MAX_PENDING = 2  # unauthenticated sockets waiting for a start message
START_LIMIT, START_WINDOW = 6, 60.0
UPDATE_PREFIX = "[Sous update]"
SDK_PROFILE = "sous-voice"
MAX_CONTENTS = 32  # per-content role/stage entries kept for interleaved output
# The only tool: zero-argument, read-only, answered by the request thread from the database.
TOOL_NAME = "get_cooking_context"
TOOL_SCHEMA = json.dumps({"type": "object", "properties": {}, "required": []})
TOOL_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
MAX_TOOL_INPUT = 1024  # bytes of accumulated toolUse content per call
MAX_PENDING_TOOLS = 4
MAX_TOOL_CALLS = 30  # per stream
CONTEXT_REQUEST = "_context_request"  # internal outbox message; never sent to the browser
TOOL_UNAVAILABLE = json.dumps({"error": "This tool is not available."})

AUTH_FAILED = "Voice could not start. Reload the page and try again."
BUSY = "Voice is busy. Try again in a moment."
# One stream instance-wide for this private release; the message names no account or session.
ALREADY_ACTIVE = "Voice guidance is already in use on this server. Stop it there or try again later."
TOO_MANY = "Voice was started too often. Wait a minute and try again."
NOT_ACTIVE = "Voice guidance needs an active cooking session."
LOCKED = "Settings are locked. Unlock them in Settings to use voice; manual controls still work."
NO_CONNECTION = "Save an AWS connection in Settings to use voice."
UNSUPPORTED_REGION = ("Nova 2 Sonic is not available in the saved AWS region. Use us-east-1, us-west-2,"
                      " eu-north-1 or ap-northeast-1.")
BAD_AUDIO = "Voice stopped: the browser sent invalid audio."
BAD_MESSAGE = "Voice stopped: unexpected message."
SLOW = "Voice stopped because the connection fell behind. Cooking controls still work."
CONNECT_FAILED = "AWS did not respond in time. Cooking controls still work; try voice again later."
STILL_STOPPING = "The previous voice session is still closing. Try again in a moment."
UNAVAILABLE = "Voice guidance is unavailable right now. Cooking controls still work."
UPSTREAM_ERRORS = (  # matched against exception/event type names only, never upstream text
    ("AccessDenied", "AWS denied access to Nova 2 Sonic. Check IAM permissions and model access in this region."),
    ("UnrecognizedClient", "AWS rejected the saved credentials. Check them in Settings."),
    ("ExpiredToken", "The saved AWS session token has expired. Update it in Settings."),
    ("Throttling", "AWS is throttling voice requests. Try again in a moment."),
    ("ServiceQuota", "AWS is throttling voice requests. Try again in a moment."),
    ("ResourceNotFound", "Nova 2 Sonic was not found. Check model access for this region."),
    ("Validation", "AWS rejected the voice request. Check that Nova 2 Sonic is enabled in this region."),
    ("ModelTimeout", "The voice session timed out. Start voice again when you're ready."),
    ("Timeout", CONNECT_FAILED),
)

PERSONA = (
    "You are Sous, a friendly hands-free cooking companion talking with a cook in their kitchen. "
    "Speak naturally in one to three short sentences, without lists or markdown. "
    "You only advise: you cannot change steps, timers, monitoring or anything in the app. If the cook "
    "asks you to, tell them to use the on-screen controls. "
    "You cannot see the kitchen and have no live video. Camera notes are earlier automated snapshots "
    "with their age; describe them as earlier observations, never as something you see now. "
    "Camera notes cannot confirm food safety; for safety questions suggest a thermometer or trusted "
    f"guidance. Messages starting with {UPDATE_PREFIX} come from the app, not the cook; respond to "
    "them briefly and do not repeat earlier camera notes unless asked. "
    f"When the cook asks about their current progress, how much time is left on a timer, or what "
    f"the camera noticed recently, call {TOOL_NAME} first and answer from its result; it only reads "
    "the app's current step, timers, latest camera note and unacknowledged camera alerts. Camera alerts "
    "are advisory and may be wrong; never say the kitchen is safe. Do not call it otherwise."
)

INTERRUPTED = re.compile(r'\{\s*"interrupted"\s*:\s*true\s*\}')
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

logger = logging.getLogger(__name__)


class VoiceUpstreamError(Exception):
    """A modeled error event from the stream. Carries only the event type name."""

    def __init__(self, event_type):
        super().__init__(event_type)
        self.event_type = event_type


def error_name(exc):
    return getattr(exc, "event_type", None) or type(exc).__name__


def new_state():
    return {"pending": threading.BoundedSemaphore(MAX_PENDING), "active": threading.Lock(),
            "starts": collections.deque(maxlen=START_LIMIT), "starts_lock": threading.Lock(),
            "lingering": None,  # bridge that outlived its shutdown budget; blocks new streams
            "open_stream": SonicStream.open}


def voice_status(vault):
    """(available, reason) from in-memory vault state only; never contacts AWS."""
    locked, conn = vault.state()
    if locked:
        return False, LOCKED if vault.initialized else NO_CONNECTION
    if not conn:
        return False, NO_CONNECTION
    if conn.get("region") not in SUPPORTED_REGIONS:
        return False, UNSUPPORTED_REGION
    return True, None


def public_error(exc):
    name = error_name(exc)
    for key, message in UPSTREAM_ERRORS:
        if key in name:
            return message
    return UNAVAILABLE


def clean_text(value, limit):
    return CONTROL_CHARS.sub(" ", value).strip()[:limit]


def format_seconds(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    if minutes and secs:
        return f"{minutes} min {secs} s"
    return f"{minutes} min" if minutes else f"{secs} s"


# --- Prompt context (built from the session's recipe snapshot) -------------

def system_prompt(recipe):
    steps = "\n".join(f"{n}. {step['title']}: {step['instruction']}"
                      for n, step in enumerate(recipe["steps"], 1))
    ingredients = ", ".join(recipe["ingredients"]) or "none listed"
    return (f"{PERSONA}\n\nRecipe: {recipe['name']}. {recipe['description']}\n"
            f"Ingredients: {ingredients}\nSteps:\n{steps}")[:MAX_CONTEXT]


def describe_state(recipe, row, at, extra=None):
    """Plain-language state; extra adds named timers and advisory alerts for the context tool."""
    steps = recipe["steps"]
    index = row["step_index"]
    step = steps[index]
    parts = [f"Current step {index + 1} of {len(steps)}: {step['title']}. {step['instruction']}"]
    if index == len(steps) - 1:
        parts.append("This is the final step; the cook finishes with the Finish button.")
    deadline = row["timer_deadline"]
    if deadline is None:
        parts.append("No timer is running.")
    elif at < deadline:
        parts.append(f"A timer has {format_seconds(deadline - at)} left.")
    else:
        parts.append("The timer has finished.")
    if not row["monitoring"]:
        parts.append("Camera monitoring is paused.")
    try:
        obs = json.loads(row["latest_observation"]) if row["latest_observation"] else None
    except ValueError:
        obs = None
    if isinstance(obs, dict) and isinstance(obs.get("activity"), str):
        created = obs.get("created_at")
        age = at - created if isinstance(created, (int, float)) else 0
        note = (f"Earlier camera note from about {format_seconds(age)} ago, not live: "
                f"{clean_text(obs['activity'], MAX_NOTE_TEXT)}; "
                f"{clean_text(str(obs.get('evidence') or ''), MAX_NOTE_TEXT)}.")
        parts.append(note)
    else:
        parts.append("No camera notes yet.")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def greeting(recipe, row, at):
    return (f"{UPDATE_PREFIX} The cook just turned on voice guidance. Greet them in one short sentence "
            f"and mention the current step. {describe_state(recipe, row, at)}")[:MAX_CONTEXT]


STATE_COLUMNS = "status, step_index, monitoring, timer_deadline, timer_alerted, latest_observation"
# Named timers whose deadline has passed, unless cancelled or dismissed before finishing. An
# acknowledgment after the deadline (the UI may ack between polls) still gets one announcement.
NAMED_DONE = ("SELECT id, name FROM session_timers WHERE session_id = ? AND deadline_at <= ?"
              " AND cancelled_at IS NULL AND (acknowledged_at IS NULL OR acknowledged_at >= deadline_at)"
              f" ORDER BY id LIMIT {cooking.MAX_TIMERS}")


class SessionWatcher:
    """Polls small session columns and turns relevant changes into one context update."""

    def __init__(self, db, session_id, recipe, row, at):
        self.db, self.session_id, self.recipe = db, session_id, recipe
        self.step = row["step_index"]
        self.deadline = row["timer_deadline"]
        self.timer_done = self.deadline if self.deadline is not None and at >= self.deadline else None
        last = db.execute("SELECT MAX(id) FROM events WHERE session_id = ?", (session_id,)).fetchone()[0]
        self.last_event = last or 0
        # Named timers that already finished before voice started are not announced again.
        self.named_done = {r["id"] for r in db.execute(NAMED_DONE, (session_id, at))}

    def poll(self, at):
        """Return (update text or None, end reason or None)."""
        row = self.db.execute(f"SELECT {STATE_COLUMNS} FROM sessions WHERE id = ?",
                              (self.session_id,)).fetchone()
        if row is None or row["status"] != "active":
            return None, "session_ended"
        notes = []
        if row["step_index"] != self.step:
            self.step = row["step_index"]
            notes.append("The cook is now on a different step. Introduce it in one or two sentences.")
        deadline = row["timer_deadline"]
        if deadline != self.deadline:
            self.deadline, self.timer_done = deadline, None
            if deadline is None:
                notes.append("The timer was cancelled.")
            elif at < deadline:
                notes.append(f"A timer was set for {format_seconds(deadline - at)}. Acknowledge it in a few words.")
        if deadline is not None and at >= deadline and self.timer_done != deadline:
            self.timer_done = deadline
            # This watcher announces its first observed deadline crossing independently of the
            # browser's timer_ack. The UI may acknowledge between polls while legacy TTS is muted.
            notes.append("The timer just finished. Tell the cook right away in one short sentence.")
        for timer in self.db.execute(NAMED_DONE, (self.session_id, at)).fetchall():
            if timer["id"] not in self.named_done:
                self.named_done.add(timer["id"])
                notes.append(f"The {clean_text(timer['name'], cooking.MAX_TIMER_NAME)} timer just finished."
                             " Tell the cook right away in one short sentence.")
        alerts = self.db.execute(
            "SELECT id, kind, message, created_at FROM events WHERE session_id = ?"
            " AND kind IN ('alert', 'safety') AND id > ? ORDER BY id DESC LIMIT 3",
            (self.session_id, self.last_event)).fetchall()
        if alerts:
            self.last_event = alerts[0]["id"]
            safety_times = {a["created_at"] for a in alerts if a["kind"] == "safety"}
            for alert in reversed(alerts):
                if alert["kind"] == "safety":
                    notes.append(f"An earlier camera snapshot flagged an advisory concern: "
                                 f"{clean_text(alert['message'], MAX_NOTE_TEXT)}. It may be wrong and you cannot "
                                 "see the kitchen now. Mention it calmly in one sentence, suggest the cook "
                                 "check for themselves, and never say the kitchen is safe.")
                elif alert["created_at"] not in safety_times:  # same sample: one announcement only
                    notes.append(f"An earlier camera snapshot raised this alert: "
                                 f"{clean_text(alert['message'], MAX_NOTE_TEXT)}. Mention it calmly in one "
                                 "sentence; you cannot see the kitchen now.")
        if not notes:
            return None, None
        text = f"{UPDATE_PREFIX} {' '.join(notes)} {describe_state(self.recipe, row, at)}"
        return text[:MAX_CONTEXT], None


# --- Nova Sonic events -----------------------------------------------------

def _event(name, body):
    return {"event": {name: body}}


def setup_events(prompt, audio_content, voice_id, instructions):
    return [
        _event("sessionStart", {"inferenceConfiguration": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.7}}),
        _event("promptStart", {
            "promptName": prompt,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": OUTPUT_SAMPLE_RATE, "sampleSizeBits": 16,
                "channelCount": 1, "voiceId": voice_id, "encoding": "base64", "audioType": "SPEECH"},
            "toolUseOutputConfiguration": {"mediaType": "application/json"},
            "toolConfiguration": {"tools": [{"toolSpec": {
                "name": TOOL_NAME,
                "description": ("Read the current cooking step, timers, latest camera note and unacknowledged"
                                " advisory camera alerts when asked."),
                "inputSchema": {"json": TOOL_SCHEMA},
            }}]},
        }),
        *text_content(prompt, "SYSTEM", instructions, interactive=False),
        _event("contentStart", {
            "promptName": prompt, "contentName": audio_content, "type": "AUDIO", "interactive": True,
            "role": "USER",
            "audioInputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": INPUT_SAMPLE_RATE, "sampleSizeBits": 16,
                "channelCount": 1, "audioType": "SPEECH", "encoding": "base64"},
        }),
    ]


def text_content(prompt, role, text, interactive):
    """One complete text content block. Interactive USER text may be sent while USER audio is open."""
    name = str(uuid.uuid4())
    return [
        _event("contentStart", {"promptName": prompt, "contentName": name, "type": "TEXT",
                                "interactive": interactive, "role": role,
                                "textInputConfiguration": {"mediaType": "text/plain"}}),
        _event("textInput", {"promptName": prompt, "contentName": name, "content": text}),
        _event("contentEnd", {"promptName": prompt, "contentName": name}),
    ]


def audio_event(prompt, audio_content, pcm):
    return _event("audioInput", {"promptName": prompt, "contentName": audio_content,
                                 "content": base64.b64encode(pcm).decode("ascii")})


def end_events(prompt, audio_content):
    return [_event("contentEnd", {"promptName": prompt, "contentName": audio_content}),
            _event("promptEnd", {"promptName": prompt}),
            _event("sessionEnd", {})]


def is_interrupted(text):
    return bool(INTERRUPTED.search(text))


def generation_stage(fields):
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except ValueError:
            return None
    return fields.get("generationStage") if isinstance(fields, dict) else None


# --- Official SDK stream ----------------------------------------------------

class _NoSharedFiles:
    """SDK config FileSystem that never reads ~/.aws or any other host file."""

    async def read_file(self, path):
        return f"[profile {SDK_PROFILE}]\n"


async def build_config(connection):
    """SDK 0.12 config: CRT HTTP/2 duplex transport and a static resolver for the saved keys.

    Every value with an environment/profile resolver is overridden, and profile files come from
    an in-memory stub, so host credentials and AWS_* variables are never consulted. No retries.
    """
    from aws_sdk_bedrock_runtime.config import AsyncBedrockRuntimeConfig
    from smithy_aws_core.identity import AWSCredentialsIdentity, StaticCredentialsResolver
    from smithy_http.aio.crt import AWSCRTHTTPClient, AWSCRTHTTPClientConfig

    region = connection.region
    if region not in SUPPORTED_REGIONS:
        raise ValueError("unsupported region")
    identity = AWSCredentialsIdentity(access_key_id=connection.access_key_id,
                                      secret_access_key=connection.secret_access_key,
                                      session_token=connection.session_token)
    transport = AWSCRTHTTPClient(client_config=AWSCRTHTTPClientConfig(force_http_2=True))
    try:
        return await AsyncBedrockRuntimeConfig.resolve(
            profile=SDK_PROFILE, fs=_NoSharedFiles(),
            region=region, endpoint_uri=f"https://bedrock-runtime.{region}.amazonaws.com",
            aws_credentials_identity_resolver=StaticCredentialsResolver(identity),
            transport=transport, retry_mode="standard", max_attempts=1, sdk_ua_app_id=None,
        )
    except BaseException:
        await _close_quietly(transport.close)
        raise


async def _close_quietly(*closers):
    for close in closers:
        try:
            await asyncio.wait_for(close(), CLOSE_STEP_TIMEOUT)
        except Exception:  # includes TimeoutError; cleanup must not mask the original outcome
            pass


async def _close_stream(stream):
    try:  # SonicStream.close bounds each of its four steps
        await asyncio.wait_for(stream.close(), 4 * CLOSE_STEP_TIMEOUT)
    except Exception:
        pass


class SonicStream:
    """InvokeModelWithBidirectionalStream as JSON event dicts. send/receive/close."""

    def __init__(self, client, transport, stream):
        self._client, self._transport, self._stream = client, transport, stream
        self._output = None

    @classmethod
    async def open(cls, connection):
        from aws_sdk_bedrock_runtime.client import AsyncBedrockRuntimeClient
        from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamOperationInput

        config = await build_config(connection)
        client = AsyncBedrockRuntimeClient(config=config)
        try:
            stream = await client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL_ID))
        except BaseException:
            await _close_quietly(client.close, config.transport.close)
            raise
        return cls(client, config.transport, stream)

    async def send(self, event):
        from aws_sdk_bedrock_runtime.models import (
            BidirectionalInputPayloadPart, InvokeModelWithBidirectionalStreamInputChunk)

        payload = json.dumps(event).encode("utf-8")
        await self._stream.input_stream.send(
            InvokeModelWithBidirectionalStreamInputChunk(value=BidirectionalInputPayloadPart(bytes_=payload)))

    async def receive(self):
        """Next event dict ({} if unusable), None at end; raises VoiceUpstreamError for error events."""
        from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamOutputChunk

        if self._output is None:
            _, self._output = await self._stream.await_output()
            if self._output is None:
                return None
        item = await self._output.receive()
        if item is None:
            return None
        if not isinstance(item, InvokeModelWithBidirectionalStreamOutputChunk):
            raise VoiceUpstreamError(type(item).__name__)
        raw = item.value.bytes_ or b""
        if len(raw) > MAX_UPSTREAM_EVENT:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def wait_ready(self):
        """Bound output-handshake readiness before the browser is told the stream is ready."""
        if self._output is None:
            _, self._output = await self._stream.await_output()
        if self._output is None:
            raise VoiceUpstreamError("OutputStreamUnavailable")

    async def close(self):
        closers = [self._stream.input_stream.close]
        if self._output is not None:
            closers.append(self._output.close)
        await _close_quietly(*closers, self._client.close, self._transport.close)


# --- Bridge: one upstream stream on a dedicated event loop thread -----------

class VoiceBridge:
    def __init__(self, open_stream, connection, voice_id, instructions, greeting_text):
        self._open_stream = open_stream
        self._connection = connection  # immutable snapshot
        self._voice_id = voice_id
        self._instructions = instructions
        self._greeting = greeting_text
        self._prompt = str(uuid.uuid4())
        self._audio_content = str(uuid.uuid4())
        self._inbox = queue.Queue(maxsize=INPUT_QUEUE_MAX)
        self.outbox = queue.Queue(maxsize=OUTBOX_MAX)
        self.finished = threading.Event()
        self.result = None
        self._stopping = threading.Event()
        self._loop_lock = threading.Lock()
        self._loop = self._wake = self._stop = None
        self._role = self._stage = None
        self._content_states = collections.OrderedDict()
        self._tool_contents = collections.OrderedDict()
        self._tool_calls = 0
        self._last_transcript = None
        self._interrupt_sent = False
        self._thread = threading.Thread(target=self._run, name="sous-voice", daemon=True)

    # Called from the request thread.

    def start(self):
        self._thread.start()

    def send_audio(self, pcm):
        return self._offer(("audio", pcm))

    def send_text(self, text):
        return self._offer(("text", text))

    def stop(self):
        self._stopping.set()
        self._notify(stop=True)

    def join(self, timeout):
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def drain(self, limit=64):
        messages = []
        while len(messages) < limit:
            try:
                messages.append(self.outbox.get_nowait())
            except queue.Empty:
                break
        return messages

    def terminal(self):
        kind, value = self.result or ("stopped", "upstream_closed")
        return {"type": "error", "message": value} if kind == "error" else {"type": "stopped", "reason": value}

    def _offer(self, item):
        try:
            self._inbox.put_nowait(item)
        except queue.Full:
            return False
        self._notify()
        return True

    def _notify(self, stop=False):
        with self._loop_lock:
            loop, wake, stopped = self._loop, self._wake, self._stop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(wake.set)
            if stop:
                loop.call_soon_threadsafe(stopped.set)
        except RuntimeError:  # loop already closed
            pass

    # Event loop thread.

    def _finish(self, kind, value):
        if self.result is None:
            self.result = (kind, value)

    def _emit(self, message):
        try:
            self.outbox.put_nowait(message)
        except queue.Full:
            self._finish("error", SLOW)
            self._stopping.set()
            self._stop.set()
            self._wake.set()

    def _run(self):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._main())
        except BaseException as exc:
            logger.error("Voice bridge failed: %s", type(exc).__name__)
            self._finish("error", UNAVAILABLE)
        finally:
            with self._loop_lock:
                self._loop = None
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.wait(pending, timeout=CLOSE_STEP_TIMEOUT))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor(CLOSE_STEP_TIMEOUT))
            except Exception as exc:
                logger.warning("Voice bridge cleanup failed: %s", type(exc).__name__)
            finally:
                loop.close()
                self.finished.set()

    async def _main(self):
        with self._loop_lock:
            self._loop = asyncio.get_running_loop()
            self._wake, self._stop = asyncio.Event(), asyncio.Event()
        if self._stopping.is_set():
            self._stop.set()
        stream = None
        try:
            ok, stream = await self._guarded(self._open_stream(self._connection))
            if not ok:
                return
            ok, _ = await self._guarded(self._setup(stream))
            if not ok:
                return
            wait_ready = getattr(stream, "wait_ready", None)
            if wait_ready is not None:
                ok, _ = await self._guarded(wait_ready())
                if not ok:
                    return
            self._emit({"type": "ready", "model": MODEL_ID, "voice": self._voice_id,
                        "input_sample_rate": INPUT_SAMPLE_RATE, "output_sample_rate": OUTPUT_SAMPLE_RATE})
            receiver = asyncio.create_task(self._receive_loop(stream))
            sender = asyncio.create_task(self._send_loop(stream))
            stopper = asyncio.create_task(self._stop.wait())
            tasks = {receiver, sender, stopper}
            done, _ = await asyncio.wait(tasks, timeout=MAX_STREAM_SECONDS + CONNECT_TIMEOUT,
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks, timeout=CLOSE_STEP_TIMEOUT)
            if not done:
                self._finish("stopped", "time_limit")
            if self._stop.is_set() and receiver not in done:
                await _close_quietly(lambda: self._send_all(stream, end_events(self._prompt, self._audio_content)))
        except Exception as exc:
            logger.warning("Voice upstream failed: %s", error_name(exc))
            self._finish("error", public_error(exc))
        finally:
            if stream is not None:
                await _close_stream(stream)
            self._finish("stopped", "upstream_closed")

    async def _guarded(self, coro):
        """Run coro until done, stop or CONNECT_TIMEOUT. Returns (completed, result)."""
        task = asyncio.ensure_future(coro)
        stopper = asyncio.ensure_future(self._stop.wait())
        try:
            done, _ = await asyncio.wait({task, stopper}, timeout=CONNECT_TIMEOUT,
                                         return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopper.cancel()
        if task in done:
            return True, task.result()
        task.cancel()
        await asyncio.wait({task}, timeout=CLOSE_STEP_TIMEOUT)
        if task.done() and not task.cancelled() and task.exception() is None and task.result() is not None:
            leaked = task.result()  # finished while being cancelled
            if hasattr(leaked, "close"):
                await _close_stream(leaked)
        if not self._stop.is_set():
            logger.warning("Voice upstream did not respond in time.")
            self._finish("error", CONNECT_FAILED)
        return False, None

    async def _send_all(self, stream, events):
        for event in events:
            await stream.send(event)

    async def _setup(self, stream):
        await self._send_all(stream, setup_events(self._prompt, self._audio_content, self._voice_id,
                                                  self._instructions))
        await self._send_all(stream, text_content(self._prompt, "USER", self._greeting, interactive=True))

    async def _send_tool_result(self, stream, content_name, tool_use_id, result):
        await self._send_all(stream, [
            _event("contentStart", {
                "promptName": self._prompt, "contentName": content_name, "type": "TOOL",
                "role": "TOOL", "interactive": False,
                "toolResultInputConfiguration": {"toolUseId": tool_use_id, "type": "TEXT",
                                                  "textInputConfiguration": {"mediaType": "text/plain"}},
            }),
            _event("toolResult", {"promptName": self._prompt, "contentName": content_name,
                                  "content": json.dumps(result, ensure_ascii=True)}),
            _event("contentEnd", {"promptName": self._prompt, "contentName": content_name}),
        ])

    async def _send_loop(self, stream):
        try:
            while not self._stop.is_set():
                try:
                    kind, value = self._inbox.get_nowait()
                except queue.Empty:
                    self._wake.clear()
                    if self._inbox.empty():
                        await self._wake.wait()
                    continue
                if kind == "audio":
                    await stream.send(audio_event(self._prompt, self._audio_content, value))
                elif kind == "text":  # one complete text block; no audio is interleaved within it
                    await self._send_all(stream, text_content(self._prompt, "USER", value, interactive=True))
                elif kind == "tool_result":
                    content_name, tool_use_id, result = value
                    await self._send_tool_result(stream, content_name, tool_use_id, result)
        except Exception as exc:
            logger.warning("Voice upstream send failed: %s", type(exc).__name__)
            self._finish("error", public_error(exc))

    async def _receive_loop(self, stream):
        try:
            while True:
                message = await stream.receive()
                if message is None:
                    self._finish("stopped", "upstream_closed")
                    return
                self.handle(message)
        except Exception as exc:
            logger.warning("Voice upstream failed: %s", error_name(exc))
            self._finish("error", public_error(exc))

    def handle(self, message):
        """Map one upstream event dict to browser messages."""
        event = message.get("event") if isinstance(message, dict) else None
        if not isinstance(event, dict):
            return
        if isinstance(event.get("contentStart"), dict):
            start = event["contentStart"]
            key = start.get("contentId") or start.get("contentName")
            state = {"role": start.get("role"), "stage": generation_stage(start.get("additionalModelFields")),
                     "type": start.get("type")}
            self._role, self._stage = state["role"], state["stage"]
            if isinstance(key, str) and key:
                self._content_states[key] = state
                self._content_states.move_to_end(key)
                while len(self._content_states) > MAX_CONTENTS:
                    self._content_states.popitem(last=False)
                if state["type"] == "TOOL":
                    if len(self._tool_contents) >= MAX_PENDING_TOOLS and key not in self._tool_contents:
                        return
                    self._tool_contents[key] = {"tool_name": None, "tool_use_id": None, "content": ""}
        elif isinstance(event.get("textOutput"), dict):
            output = event["textOutput"]
            key = output.get("contentId") or output.get("contentName")
            state = self._content_states.get(key) if isinstance(key, str) else None
            self._text_output(output, state)
        elif isinstance(event.get("audioOutput"), dict):
            self._audio_output(event["audioOutput"].get("content"))
        elif isinstance(event.get("toolUse"), dict):
            self._tool_fragment(event["toolUse"])
        elif isinstance(event.get("contentEnd"), dict):
            end = event["contentEnd"]
            if end.get("stopReason") == "INTERRUPTED":
                self._interrupted()
            key = end.get("contentId") or end.get("contentName")
            if isinstance(key, str):
                if key in self._tool_contents:
                    if end.get("stopReason") == "TOOL_USE":
                        if end.get("type") == "TOOL":
                            self._finish_tool(key)
                        else:
                            self._tool_contents.pop(key, None)
                    else:
                        self._tool_contents.pop(key, None)
                self._content_states.pop(key, None)

    def _interrupted(self):
        if not self._interrupt_sent:  # textOutput marker and contentEnd may both report it
            self._interrupt_sent = True
            self._emit({"type": "interrupted"})

    def _text_output(self, output, state=None):
        content = output.get("content")
        if not isinstance(content, str):
            return
        if is_interrupted(content):
            self._interrupted()
            return
        role = output.get("role") or (state or {}).get("role") or self._role
        text = clean_text(content, MAX_TRANSCRIPT)
        if not text:
            return
        if role == "USER":
            if text.startswith(UPDATE_PREFIX):  # echo of app context, not the cook's words
                return
            final = True
        elif role == "ASSISTANT":
            final = (state or {}).get("stage", self._stage) != "SPECULATIVE"
        else:
            return
        key = (role, text, final)
        if key == self._last_transcript:
            return
        self._last_transcript = key
        self._emit({"type": "transcript", "role": role.lower(), "text": text, "final": final})

    def _tool_fragment(self, part):
        key = part.get("contentId") or part.get("contentName")
        if not isinstance(key, str) or not key:
            return
        item = self._tool_contents.get(key)
        if item is None:
            if len(self._tool_contents) >= MAX_PENDING_TOOLS:
                return
            item = {"tool_name": None, "tool_use_id": None, "content": ""}
            self._tool_contents[key] = item
        name, tool_id, content = part.get("toolName"), part.get("toolUseId"), part.get("content", "")
        if isinstance(name, str):
            item["tool_name"] = name
        if isinstance(tool_id, str):
            item["tool_use_id"] = tool_id
        current_size = len(item["content"].encode("utf-8", "replace"))
        content_size = len(content.encode("utf-8", "replace")) if isinstance(content, str) else MAX_TOOL_INPUT + 1
        if not isinstance(content, str) or current_size + content_size > MAX_TOOL_INPUT:
            item["content"] = "!invalid!"
        elif item["content"] != "!invalid!":
            item["content"] += content

    def _finish_tool(self, key):
        item = self._tool_contents.pop(key, None)
        if item is None:
            return
        self._tool_calls += 1
        tool_id = item["tool_use_id"]
        valid_id = isinstance(tool_id, str) and TOOL_ID.fullmatch(tool_id)
        valid_name = item["tool_name"] == TOOL_NAME
        try:
            args = json.loads(item["content"] or "{}")
        except (ValueError, TypeError):
            args = None
        valid_args = isinstance(args, dict) and not args
        if self._tool_calls > MAX_TOOL_CALLS:
            result = {"error": "Request limit reached."}
        elif not valid_name or not valid_args or item["content"] == "!invalid!":
            result = {"error": "Unsupported or invalid read-only request."}
        else:
            result = None  # controller reads fresh server state on its request thread
        if not valid_id:
            return
        if result is None:
            self._emit({"type": CONTEXT_REQUEST, "content_name": str(uuid.uuid4()), "tool_use_id": tool_id})
        else:
            self._emit({"type": CONTEXT_REQUEST, "content_name": str(uuid.uuid4()), "tool_use_id": tool_id,
                        "result": result})

    def answer_context(self, content_name, tool_use_id, result):
        return self._offer(("tool_result", (content_name, tool_use_id, result)))

    def _audio_output(self, content):
        if not isinstance(content, str) or len(content) > MAX_OUTPUT_AUDIO * 4 // 3 + 4:
            return
        try:
            raw = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            return
        if not raw or len(raw) % 2:
            return
        self._interrupt_sent = False
        self._emit({"type": "audio", "audio": base64.b64encode(raw).decode("ascii")})


# --- WebSocket controller (request thread) ----------------------------------

def serve(ws, session_id, expected_csrf, db, voice_id, vault, user_id, auth_valid):
    """Handle one voice WebSocket for an authenticated account. Always ends with a stopped/error
    message unless disconnected.

    vault is that account's Vault; only sessions owned by user_id are served. auth_valid() reports
    whether the caller's login is still live and is polled while streaming.
    """
    try:
        terminal = _serve(ws, session_id, expected_csrf, db, voice_id, vault, user_id, auth_valid)
    except ConnectionClosed:
        return
    except Exception as exc:
        logger.error("Voice socket failed: %s", type(exc).__name__)
        terminal = {"type": "error", "message": UNAVAILABLE}
    try:
        ws.send(json.dumps(terminal))
    except Exception:  # client already gone
        pass


def _error(message):
    return {"type": "error", "message": message}


def _serve(ws, session_id, expected_csrf, db, voice_id, vault, user_id, auth_valid):
    state = current_app.extensions["sous_voice"]
    if not state["pending"].acquire(blocking=False):
        return _error(BUSY)
    try:
        authenticated = _authenticate(ws, expected_csrf) and auth_valid()
    finally:
        state["pending"].release()
    if not authenticated:
        return _error(AUTH_FAILED)

    row = db.execute("SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).fetchone()
    if row is None or row["status"] != "active" or not row["recipe_snapshot"]:
        return _error(NOT_ACTIVE)
    # One atomic lease: any later lock/unlock/save changes the epoch, even with identical keys.
    epoch, connection = vault.lease()
    if connection is None:
        return _error(voice_status(vault)[1] or NO_CONNECTION)  # locked or nothing saved
    if connection.region not in SUPPORTED_REGIONS:
        return _error(UNSUPPORTED_REGION)
    if voice_id not in VOICES:
        voice_id = DEFAULT_VOICE
    if not state["active"].acquire(blocking=False):
        return _error(ALREADY_ACTIVE)
    try:
        lingering = state.get("lingering")
        if lingering is not None:
            if lingering._thread.is_alive():
                return _error(STILL_STOPPING)
            state["lingering"] = None
        if not _allow_start(state):
            return _error(TOO_MANY)
        return _run(ws, state, state["open_stream"], db, session_id, row, vault, epoch, connection, voice_id,
                    auth_valid)
    finally:
        state["active"].release()


def _authenticate(ws, expected):
    message = ws.receive(timeout=START_TIMEOUT)
    if not isinstance(message, str) or len(message) > MAX_CONTROL_BYTES:
        return False
    body = _json_object(message)
    if body is None or set(body) != {"type", "csrf_token"} or body["type"] != "start":
        return False
    token = body["csrf_token"]
    if not expected or not isinstance(token, str):
        return False
    return hmac.compare_digest(expected.encode("utf-8", "replace"), token.encode("utf-8", "replace"))


def _json_object(text):
    try:
        body = json.loads(text)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _allow_start(state):
    now = time.monotonic()
    with state["starts_lock"]:
        starts = state["starts"]
        if len(starts) == START_LIMIT and now - starts[0] < START_WINDOW:
            return False
        starts.append(now)
        return True


def _valid_pcm(data):
    return 2 <= len(data) <= MAX_AUDIO_FRAME and len(data) % 2 == 0


def _run(ws, state, open_stream, db, session_id, row, vault, epoch, connection, voice_id, auth_valid):
    recipe = json.loads(row["recipe_snapshot"])
    clock = current_app.config["CLOCK"]
    at = clock()
    watcher = SessionWatcher(db, session_id, recipe, row, at)
    bridge = VoiceBridge(open_stream, connection, voice_id, system_prompt(recipe), greeting(recipe, row, at))
    bridge.start()
    started = last_audio = time.monotonic()
    next_poll = started + POLL_INTERVAL
    ready = False
    terminal = None
    try:
        while terminal is None:
            finished = bridge.finished.is_set()
            for message in bridge.drain():
                if message["type"] == CONTEXT_REQUEST:
                    if "result" in message:
                        result = message["result"]
                    else:
                        fresh = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
                        if fresh is None or fresh["status"] != "active" or not fresh["recipe_snapshot"]:
                            result = {"error": NOT_ACTIVE}
                        else:
                            fresh_recipe = json.loads(fresh["recipe_snapshot"])
                            at = clock()  # read-only: named timers and unacknowledged advisory alerts
                            tools = cooking.voice_context(db, session_id, at, clean_text, format_seconds)
                            result = {"context": describe_state(fresh_recipe, fresh, at, tools)[:MAX_CONTEXT]}
                    if not bridge.answer_context(message["content_name"], message["tool_use_id"], result):
                        terminal = _error(SLOW)
                    continue  # internal request: never expose it to the browser
                if message["type"] == "ready":
                    ready, last_audio = True, time.monotonic()
                ws.send(json.dumps(message))
            if finished:
                if bridge.outbox.empty():
                    terminal = bridge.terminal()
                continue
            now = time.monotonic()
            current_epoch, current = vault.lease()  # in-memory only; never an ambient lookup
            if now - started >= MAX_STREAM_SECONDS:
                terminal = {"type": "stopped", "reason": "time_limit"}
            elif not ready and now - started > CONNECT_TIMEOUT + 1:
                terminal = _error(CONNECT_FAILED)
            elif ready and now - last_audio > IDLE_TIMEOUT:
                terminal = {"type": "stopped", "reason": "idle"}
            elif current_epoch != epoch:  # lock, unlock or save since the lease, even with equal keys
                terminal = {"type": "stopped", "reason": "locked" if current is None else "settings_changed"}
            elif now >= next_poll:
                next_poll = now + POLL_INTERVAL
                if not auth_valid():  # logout, password change or reset revoked this login
                    terminal = {"type": "stopped", "reason": "signed_out"}
                    break
                update, end = watcher.poll(clock())
                if end:
                    terminal = {"type": "stopped", "reason": end}
                elif update and ready and not bridge.send_text(update):
                    terminal = _error(SLOW)
            if terminal is not None:
                break
            message = ws.receive(timeout=TICK)
            if message is None:
                continue
            if isinstance(message, (bytes, bytearray)):
                if not _valid_pcm(message):
                    terminal = _error(BAD_AUDIO)
                elif ready:  # audio before ready is dropped, never buffered
                    if not bridge.send_audio(bytes(message)):
                        terminal = _error(SLOW)
                    last_audio = time.monotonic()
            elif len(message) <= MAX_CONTROL_BYTES and _json_object(message) == {"type": "stop"}:
                terminal = {"type": "stopped", "reason": "client_stop"}
            else:
                terminal = _error(BAD_MESSAGE)
    finally:
        bridge.stop()
        if not bridge.join(SHUTDOWN_TIMEOUT):
            logger.warning("Voice bridge did not stop within %s s.", SHUTDOWN_TIMEOUT)
            state["lingering"] = bridge
    return terminal
