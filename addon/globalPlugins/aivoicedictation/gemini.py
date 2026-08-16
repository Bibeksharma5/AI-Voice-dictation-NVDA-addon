# -*- coding: UTF-8 -*-
# AI voice dictation - Google Gemini API client.
# This module is intentionally free of NVDA imports so that it can be tested
# with a plain Python interpreter.
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

#: Base URL of the Gemini API.
API_BASE = "https://generativelanguage.googleapis.com/v1beta"

#: Mapping from the legacy settings values to Gemini model IDs. Kept for
#: backwards compatibility with configurations saved before the settings
#: combo box started listing real model IDs ("flash" and "lite").
LEGACY_MODEL_IDS = {
	"lite": "gemini-3.5-flash-lite",
	"flash": "gemini-3.5-flash",
}

#: Model used when the configured value is empty.
DEFAULT_MODEL = "gemini-3.5-flash"

#: Default model list shown in the settings combo box before the user fetches
#: the latest models from the API. These are the current general-purpose
#: text/audio models; the "Fetch models" button replaces this list with
#: whatever the API reports.
DEFAULT_MODELS = [
	"gemini-3.7-flash",
	"gemini-3.6-flash",
	"gemini-3.5-flash",
	"gemini-3.5-flash-lite",
	"gemini-3.1-flash-lite",
	"gemini-2.5-pro",
	"gemini-2.5-flash",
	"gemini-2.5-flash-lite",
]

#: Tokens that identify specialized models which do not produce general text
#: output (image generation, speech synthesis, video, embeddings, agents
#: etc.). They are filtered out of the fetched model list so the combo box
#: only offers models usable for dictation, transcription and text
#: processing.
_EXCLUDED_MODEL_TOKENS = (
	"-image",
	"-tts",
	"embedding",
	"veo",
	"lyria",
	"robotics",
	"computer-use",
	"omni",
	"live-translate",
	"antigravity",
	"deep-research",
)


def resolve_model(model):
	"""Return the model ID to use for a stored settings value.

	Legacy values ("flash", "lite") are mapped to their real model IDs;
	every other value (including real model IDs) is passed through; an empty
	value falls back to :data:`DEFAULT_MODEL`.
	"""
	if not model:
		return DEFAULT_MODEL
	return LEGACY_MODEL_IDS.get(model, model)

#: MIME type for an audio file, guessed from its extension. Used when
#: transcribing audio files selected in File Explorer.
AUDIO_MIME_BY_EXTENSION = {
	".wav": "audio/wav",
	".wave": "audio/wav",
	".mp3": "audio/mpeg",
	".m4a": "audio/mp4",
	".m4b": "audio/mp4",
	".mp4": "audio/mp4",
	".aac": "audio/aac",
	".ogg": "audio/ogg",
	".oga": "audio/ogg",
	".opus": "audio/ogg",
	".flac": "audio/flac",
	".aiff": "audio/aiff",
	".aif": "audio/aiff",
	".aifc": "audio/aiff",
	".webm": "audio/webm",
	".amr": "audio/amr",
}


def audio_mime_type(path):
	"""Return the best-guess MIME type for an audio file path."""
	extension = os.path.splitext(path)[1].lower()
	return AUDIO_MIME_BY_EXTENSION.get(extension, "audio/wav")


def is_audio_file(path):
	"""Return True if the file's extension is a known audio type.

	Used to reject non-audio files before they are uploaded to the API.
	"""
	extension = os.path.splitext(path)[1].lower()
	return extension in AUDIO_MIME_BY_EXTENSION

#: Timeout for a single API request in seconds.
REQUEST_TIMEOUT = 180

#: Delay in seconds before retrying with the next API key.
RETRY_DELAY = 0.5

SYSTEM_TRANSCRIBE = (
	"You are an expert speech to text engine. Transcribe the speech in the "
	"attached audio exactly as it was spoken, preserving the exact words, "
	"natural punctuation, names and numbers. Keep consecutive sentences in "
	"the same paragraph without line breaks. Start a new line only when the "
	"speaker makes a clear long pause between separate ideas (a new "
	"paragraph) or when the speaker lists separate items (such as \"one, two, "
	"three\"), putting each listed item on its own line. Do not summarize, "
	"translate, or add any commentary. Output only the transcription text."
)
USER_TRANSCRIBE = (
	"Transcribe the speech in this audio recording exactly as it was spoken. "
	"Keep consecutive sentences together on the same line. Start a new line "
	"only for a clear long pause between separate ideas or for each item of "
	"a spoken list. Output only the transcription."
)

#: Prompts for transcribing an uploaded audio file (the ``b`` command). More
#: detailed than the microphone prompts: the file may be long, may contain
#: technical terms and should be transcribed as accurately as possible.
SYSTEM_TRANSCRIBE_FILE = (
	"You are an expert audio transcription engine. Transcribe the audio file "
	"accurately, capturing every word exactly as spoken, including names, "
	"numbers, technical terms and natural punctuation. Preserve the natural "
	"structure of the speech: keep consecutive sentences on the same line, "
	"and start a new line only when the speaker makes a clear long pause "
	"between separate ideas or when the speaker lists separate items, "
	"putting each listed item on its own line. Do not summarize, translate, "
	"correct the speaker, or add any commentary, headings or labels. Output "
	"only the transcription text."
)
USER_TRANSCRIBE_FILE = (
	"Transcribe the speech in this audio file exactly as it was spoken. "
	"Preserve names, numbers and technical terms. Keep consecutive "
	"sentences together on the same line. Start a new line only for a clear "
	"long pause between separate ideas or for each item of a spoken list. "
	"Output only the transcription."
)

SYSTEM_REFINE = (
	"You are an expert editor. Correct spelling and grammar mistakes in the "
	"provided text while preserving the original meaning, tone, wording and "
	"line breaks as much as possible. Make only the changes that are "
	"necessary. Output only the corrected text."
)
USER_REFINE = (
	"Correct the spelling and grammar of the following text:\n{text}"
)

SYSTEM_TRANSLATE = (
	"You are an expert professional translator. Translate the provided text "
	"into {language}. Preserve the meaning, tone and formatting of the original "
	"text. Output only the translated text."
)
USER_TRANSLATE = (
	"Translate the following text into {language}:\n{text}"
)

SYSTEM_EMOJIS = (
	"You are a creative writing assistant. Add appropriate emojis to the "
	"provided text to make it more expressive and engaging. Place emojis "
	"naturally at suitable points in the text. Keep the original wording, "
	"meaning and line breaks unchanged. Output only the formatted text."
)
USER_EMOJIS = (
	"Add emojis to the following text:\n{text}"
)


class GeminiAPIError(Exception):
	"""Raised when a single Gemini API request fails.

	:ivar category: One of "exhausted", "invalid_key", "permission",
		"not_found", "model_error", "network", "server" or "other".
	:ivar status_code: The HTTP status code, or ``None``.
	:ivar api_status: The status string reported by the API, or ``""``.
	"""

	def __init__(
		self,
		message,
		*,
		status_code=None,
		api_status="",
		category="other",
	):
		super().__init__(message)
		self.message = message
		self.status_code = status_code
		self.api_status = api_status
		self.category = category


class AllKeysFailedError(Exception):
	"""Raised when every configured API key failed for a request."""

	def __init__(self, last_error):
		super().__init__("All Gemini API keys failed")
		#: The :class:`GeminiAPIError` from the last attempted key,
		#: or ``None`` if no keys were configured.
		self.last_error = last_error


def _urlopen(request, timeout=REQUEST_TIMEOUT):
	return urllib.request.urlopen(request, timeout=timeout)


def _post_json(url, payload, timeout=REQUEST_TIMEOUT):
	"""POST a JSON payload and return ``(status_code, body_bytes)``.

	HTTP errors are returned as ``(status, body)`` rather than raised so that
	the caller can inspect the API error payload. Network level failures raise
	a :class:`GeminiAPIError` with the ``network`` category.
	"""
	data = json.dumps(payload).encode("utf-8")
	request = urllib.request.Request(
		url,
		data=data,
		headers={"Content-Type": "application/json"},
		method="POST",
	)
	try:
		with _urlopen(request, timeout=timeout) as response:
			return response.status, response.read()
	except urllib.error.HTTPError as e:
		try:
			body = e.read()
		except Exception:
			body = b""
		return e.code, body
	except (urllib.error.URLError, TimeoutError, OSError) as e:
		raise GeminiAPIError(
			"Network error: %s" % e,
			category="network",
		)


def _get_json(url, timeout=REQUEST_TIMEOUT):
	"""GET a URL and return ``(status_code, body_bytes)``.

	Like :func:`_post_json`, HTTP errors are returned as ``(status, body)``
	and network level failures raise a :class:`GeminiAPIError` with the
	``network`` category.
	"""
	request = urllib.request.Request(
		url,
		headers={"Content-Type": "application/json"},
		method="GET",
	)
	try:
		with _urlopen(request, timeout=timeout) as response:
			return response.status, response.read()
	except urllib.error.HTTPError as e:
		try:
			body = e.read()
		except Exception:
			body = b""
		return e.code, body
	except (urllib.error.URLError, TimeoutError, OSError) as e:
		raise GeminiAPIError(
			"Network error: %s" % e,
			category="network",
		)


def _is_usable_model(model_id):
	"""Return True if a model ID is a general-purpose text model."""
	lower = model_id.lower()
	return not any(token in lower for token in _EXCLUDED_MODEL_TOKENS)


def _order_models(model_ids):
	"""Order fetched model IDs: known current models first, then the rest.

	The API returns models without a user friendly ordering, so the well
	known current models are listed first (newest first) and any remaining
	models are appended alphabetically.
	"""
	ordered = [model for model in DEFAULT_MODELS if model in model_ids]
	rest = sorted(
		set(model_ids) - set(ordered),
	)
	return ordered + rest


def list_models(api_keys, timeout=REQUEST_TIMEOUT):
	"""Fetch the IDs of all usable Gemini models for the given API keys.

	Rotates through the API keys like every other request: if one key is
	exhausted or fails, the next key is tried. Returns the model IDs that
	support ``generateContent`` (text/audio) and are not specialized
	generative models (image, TTS, video, embeddings, etc.), with the
	well-known current models listed first.

	:raises AllKeysFailedError: when no keys are configured or every key
		failed. The ``last_error`` attribute carries the last failure.
	"""
	keys = [key.strip() for key in api_keys.split(",") if key.strip()]
	if not keys:
		raise AllKeysFailedError(None)
	last_error = None
	for index, key in enumerate(keys):
		try:
			return _list_models_with_key(key, timeout=timeout)
		except GeminiAPIError as e:
			last_error = e
			if index < len(keys) - 1:
				time.sleep(RETRY_DELAY)
	raise AllKeysFailedError(last_error)


def _list_models_with_key(key, timeout=REQUEST_TIMEOUT):
	"""Fetch the model list with a single API key."""
	url = "%s/models?key=%s" % (
		API_BASE,
		urllib.parse.quote(key, safe=""),
	)
	status, body = _get_json(url, timeout=timeout)
	if status != 200:
		raise _error_from_response(status, body)
	try:
		data = json.loads(body.decode("utf-8", errors="replace"))
	except Exception:
		raise GeminiAPIError(
			"Invalid response from the Gemini API.",
			status_code=status,
			category="other",
		)
	model_ids = []
	for model in data.get("models") or []:
		name = model.get("name", "") or ""
		if not name.startswith("models/"):
			continue
		model_id = name[len("models/"):]
		methods = model.get("supportedGenerationMethods") or []
		if "generateContent" not in methods:
			continue
		if not _is_usable_model(model_id):
			continue
		model_ids.append(model_id)
	return _order_models(model_ids)


def _error_from_response(status, body):
	"""Build a :class:`GeminiAPIError` from an API error response."""
	api_status = ""
	message = "HTTP %d" % status
	try:
		data = json.loads(body.decode("utf-8", errors="replace"))
		error = data.get("error") or {}
		api_status = error.get("status", "") or ""
		message = error.get("message", "") or message
	except Exception:
		pass
	if status == 429 or api_status == "RESOURCE_EXHAUSTED":
		category = "exhausted"
	elif api_status in ("API_KEY_INVALID", "API_KEY_NOT_FOUND") or "API key" in message:
		category = "invalid_key"
	elif status == 403 or api_status == "PERMISSION_DENIED":
		category = "permission"
	elif status == 404 or api_status == "NOT_FOUND":
		category = "not_found"
	elif status >= 500:
		category = "server"
	elif status == 400 and "model" in message.lower():
		category = "model_error"
	else:
		category = "other"
	return GeminiAPIError(
		message,
		status_code=status,
		api_status=api_status,
		category=category,
	)


def _normalize_text(text):
	"""Fix line breaks in the model's raw output.

	The model sometimes returns literal ``\\n`` escape sequences instead of
	real newlines. Real newlines arrive already decoded by the JSON parser,
	so this converts any remaining literal backslash-n sequences so that
	multi-line text survives every operation (transcription, refining,
	translation and emojis).
	"""
	return text.replace("\\n", "\n")


def _extract_text(data):
	"""Extract the model's text from a generateContent response."""
	candidates = data.get("candidates") or []
	for candidate in candidates:
		content = candidate.get("content") or {}
		parts = content.get("parts") or []
		text = "".join(
			part.get("text", "") for part in parts if isinstance(part, dict)
		)
		if text.strip():
			return _normalize_text(text)
	if data.get("promptFeedback"):
		raise GeminiAPIError(
			"The Gemini API blocked the request.",
			category="other",
		)
	raise GeminiAPIError(
		"The Gemini API returned no text.",
		category="other",
	)


class GeminiClient(object):
	"""Client for the Gemini generateContent endpoint with key rotation.

	The configured API keys are tried in order. If a request fails with one
	key, the next key is attempted until one succeeds. If all keys fail, an
	:class:`AllKeysFailedError` is raised.
	"""

	def __init__(self, api_keys, model=DEFAULT_MODEL):
		#: Individual API keys; whitespace is stripped and empties removed.
		self.api_keys = [
			key.strip() for key in api_keys.split(",") if key.strip()
		]
		self.model = model
		self.model_id = resolve_model(model)

	def transcribe(self, audio_bytes, mime_type="audio/wav"):
		"""Transcribe microphone audio bytes into text.

		:param mime_type: The MIME type of the audio (for example
			``audio/wav`` for microphone recordings or ``audio/mpeg`` for an
			MP3 file selected in File Explorer).
		"""
		parts = [
			{
				"inline_data": {
					"mime_type": mime_type,
					"data": base64.b64encode(audio_bytes).decode("ascii"),
				}
			},
			{"text": USER_TRANSCRIBE},
		]
		return self.generate(parts, system=SYSTEM_TRANSCRIBE, temperature=0.1)

	def transcribe_file(self, audio_bytes, mime_type):
		"""Transcribe an uploaded audio file into text.

		Uses the dedicated, more detailed file-transcription prompt for the
		best possible accuracy on longer recordings.

		:param mime_type: The MIME type of the audio file (for example
			``audio/mpeg`` for an MP3).
		"""
		parts = [
			{
				"inline_data": {
					"mime_type": mime_type,
					"data": base64.b64encode(audio_bytes).decode("ascii"),
				}
			},
			{"text": USER_TRANSCRIBE_FILE},
		]
		return self.generate(
			parts, system=SYSTEM_TRANSCRIBE_FILE, temperature=0.1
		)

	def refine(self, text):
		"""Correct the spelling and grammar of text."""
		return self.generate(
			[{"text": USER_REFINE.format(text=text)}],
			system=SYSTEM_REFINE,
			temperature=0.2,
		)

	def format_with_emojis(self, text):
		"""Format text with appropriate emojis."""
		return self.generate(
			[{"text": USER_EMOJIS.format(text=text)}],
			system=SYSTEM_EMOJIS,
			temperature=0.6,
		)

	def translate(self, text, target_language):
		"""Translate text into the target language."""
		return self.generate(
			[{"text": USER_TRANSLATE.format(language=target_language, text=text)}],
			system=SYSTEM_TRANSLATE.format(language=target_language),
			temperature=0.2,
		)

	def generate(self, parts, system=None, temperature=0.2):
		"""Send a generateContent request, rotating through all API keys."""
		if not self.api_keys:
			raise AllKeysFailedError(None)
		payload = {
			"contents": [{"role": "user", "parts": parts}],
			"generationConfig": {"temperature": temperature},
		}
		if system:
			payload["systemInstruction"] = {"parts": [{"text": system}]}
		last_error = None
		for index, key in enumerate(self.api_keys):
			try:
				return self._generate_with_key(key, payload)
			except GeminiAPIError as e:
				last_error = e
				if index < len(self.api_keys) - 1:
					time.sleep(RETRY_DELAY)
		raise AllKeysFailedError(last_error)

	def _generate_with_key(self, key, payload):
		url = "%s/models/%s:generateContent?key=%s" % (
			API_BASE,
			self.model_id,
			urllib.parse.quote(key, safe=""),
		)
		status, body = _post_json(url, payload)
		if status != 200:
			raise _error_from_response(status, body)
		try:
			data = json.loads(body.decode("utf-8", errors="replace"))
		except Exception:
			raise GeminiAPIError(
				"Invalid response from the Gemini API.",
				status_code=status,
				category="other",
			)
		return _extract_text(data)
