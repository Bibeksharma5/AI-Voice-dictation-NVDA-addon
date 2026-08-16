# -*- coding: UTF-8 -*-
"""Unit tests for the Gemini client (key parsing, rotation, error mapping)."""
import base64
import json
import os
import sys
import unittest

sys.path.insert(
	0,
	os.path.join(
		os.path.dirname(__file__),
		"..",
		"addon",
		"globalPlugins",
		"aivoicedictation",
	),
)

import gemini  # noqa: E402


def _success_body(text="hello"):
	return 200, json.dumps(
		{"candidates": [{"content": {"parts": [{"text": text}]}}]}
	).encode("utf-8")


class FakePoster(object):
	"""Replaces gemini._post_json with scripted responses."""

	def __init__(self, responses):
		self.responses = list(responses)
		self.calls = []
		self.last_payload = None
		self.last_url = None

	def __call__(self, url, payload, timeout=None):
		self.calls.append(url)
		self.last_payload = payload
		self.last_url = url
		status, body = self.responses.pop(0)
		return status, body


class GeminiClientTest(unittest.TestCase):

	def test_key_parsing(self):
		client = gemini.GeminiClient("  key1 ,, key2 , key3 ", "flash")
		self.assertEqual(client.api_keys, ["key1", "key2", "key3"])
		self.assertEqual(client.model_id, "gemini-3.5-flash")

	def test_model_resolution(self):
		# Legacy settings values still map to their real model IDs.
		self.assertEqual(
			gemini.GeminiClient("k", "lite").model_id,
			"gemini-3.5-flash-lite",
		)
		self.assertEqual(
			gemini.GeminiClient("k", "flash").model_id,
			"gemini-3.5-flash",
		)
		# Real model IDs pass through untouched.
		self.assertEqual(
			gemini.GeminiClient("k", "gemini-3.7-flash").model_id,
			"gemini-3.7-flash",
		)
		self.assertEqual(
			gemini.GeminiClient("k", "gemini-2.5-pro").model_id,
			"gemini-2.5-pro",
		)
		# An empty value falls back to the default model.
		self.assertEqual(
			gemini.GeminiClient("k", "").model_id,
			gemini.DEFAULT_MODEL,
		)
		self.assertEqual(gemini.resolve_model("flash"), "gemini-3.5-flash")
		self.assertEqual(gemini.resolve_model("lite"), "gemini-3.5-flash-lite")
		self.assertEqual(gemini.resolve_model("gemini-3.7-flash"), "gemini-3.7-flash")
		self.assertEqual(gemini.resolve_model(""), gemini.DEFAULT_MODEL)

	def test_no_keys_raises_immediately(self):
		client = gemini.GeminiClient("", "flash")
		with self.assertRaises(gemini.AllKeysFailedError) as ctx:
			client.refine("hello")
		self.assertIsNone(ctx.exception.last_error)

	def test_success(self):
		poster = FakePoster([_success_body("fixed text")])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		result = client.refine("helo")
		self.assertEqual(result, "fixed text")
		self.assertEqual(len(poster.calls), 1)
		self.assertIn("key=key1", poster.last_url)
		self.assertIn("gemini-3.5-flash", poster.last_url)

	def test_rotation_on_exhausted(self):
		exhausted = 429, json.dumps(
			{"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}
		).encode("utf-8")
		poster = FakePoster([exhausted, _success_body("ok")])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1,key2", "flash")
		result = client.translate("hello", "Nepali")
		self.assertEqual(result, "ok")
		self.assertEqual(len(poster.calls), 2)
		self.assertIn("key=key1", poster.calls[0])
		self.assertIn("key=key2", poster.calls[1])
		# The target language must be part of the request.
		self.assertIn("Nepali", json.dumps(poster.last_payload))

	def test_all_keys_failed_reports_last_error(self):
		exhausted = 429, json.dumps(
			{"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}
		).encode("utf-8")
		poster = FakePoster([exhausted, exhausted])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1,key2", "flash")
		with self.assertRaises(gemini.AllKeysFailedError) as ctx:
			client.refine("hello")
		self.assertEqual(ctx.exception.last_error.category, "exhausted")
		self.assertEqual(len(poster.calls), 2)

	def test_invalid_key_category(self):
		body = json.dumps(
			{"error": {"status": "API_KEY_INVALID", "message": "API key not valid."}}
		).encode("utf-8")
		poster = FakePoster([(400, body)])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		with self.assertRaises(gemini.AllKeysFailedError) as ctx:
			client.refine("hello")
		self.assertEqual(ctx.exception.last_error.category, "invalid_key")

	def test_network_error_category(self):
		def network_error(url, payload, timeout=None):
			raise gemini.GeminiAPIError("Network error", category="network")

		gemini._post_json = network_error
		client = gemini.GeminiClient("key1", "flash")
		with self.assertRaises(gemini.AllKeysFailedError) as ctx:
			client.format_with_emojis("hello")
		self.assertEqual(ctx.exception.last_error.category, "network")

	def test_transcribe_payload_has_inline_audio(self):
		poster = FakePoster([_success_body("transcribed")])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "lite")
		wav = b"\x00\x01\x02\x03"
		result = client.transcribe(wav)
		self.assertEqual(result, "transcribed")
		parts = poster.last_payload["contents"][0]["parts"]
		inline = parts[0]["inline_data"]
		self.assertEqual(inline["mime_type"], "audio/wav")
		self.assertEqual(
			base64.b64decode(inline["data"]),
			wav,
		)
		self.assertIn("gemini-3.5-flash-lite", poster.last_url)

	def test_literal_backslash_n_is_converted_to_newlines(self):
		# The model sometimes returns literal \n escape sequences; they must
		# become real newlines so multi-line dictation survives.
		poster = FakePoster(
			[
				_success_body(
					"Line one\\nLine two\\nLine three"
				)
			]
		)
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		result = client.transcribe(b"\x00\x01")
		self.assertEqual(result, "Line one\nLine two\nLine three")

	def test_real_newlines_from_json_are_preserved(self):
		# Newlines decoded by the JSON parser must pass through untouched.
		poster = FakePoster([_success_body("Line one\nLine two")])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		result = client.refine("Line one. Line two.")
		self.assertEqual(result, "Line one\nLine two")

	def test_audio_mime_type_mapping(self):
		self.assertEqual(gemini.audio_mime_type("clip.mp3"), "audio/mpeg")
		self.assertEqual(gemini.audio_mime_type("clip.M4A"), "audio/mp4")
		self.assertEqual(gemini.audio_mime_type("clip.wav"), "audio/wav")
		self.assertEqual(gemini.audio_mime_type("clip.flac"), "audio/flac")
		self.assertEqual(gemini.audio_mime_type("clip.ogg"), "audio/ogg")
		# Unknown extensions fall back to wav.
		self.assertEqual(gemini.audio_mime_type("clip.xyz"), "audio/wav")
		self.assertEqual(gemini.audio_mime_type("no-extension"), "audio/wav")

	def test_transcribe_file_uses_given_mime_type(self):
		poster = FakePoster([_success_body("transcribed")])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		result = client.transcribe(b"\x00\x01", mime_type="audio/mpeg")
		self.assertEqual(result, "transcribed")
		inline = poster.last_payload["contents"][0]["parts"][0][
			"inline_data"
		]
		self.assertEqual(inline["mime_type"], "audio/mpeg")

	def test_is_audio_file(self):
		self.assertTrue(gemini.is_audio_file("clip.mp3"))
		self.assertTrue(gemini.is_audio_file("clip.WAV"))
		self.assertTrue(gemini.is_audio_file("clip.flac"))
		self.assertTrue(gemini.is_audio_file("clip.ogg"))
		self.assertTrue(gemini.is_audio_file("clip.m4a"))
		self.assertTrue(gemini.is_audio_file("clip.aac"))
		# Non-audio files must be rejected before any API call.
		self.assertFalse(gemini.is_audio_file("notes.txt"))
		self.assertFalse(gemini.is_audio_file("image.png"))
		self.assertFalse(gemini.is_audio_file("archive.zip"))
		self.assertFalse(gemini.is_audio_file("no-extension"))

	def test_transcribe_file_uses_dedicated_prompt_and_mime(self):
		poster = FakePoster([_success_body("file transcription")])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		result = client.transcribe_file(b"\x00\x01", "audio/mpeg")
		self.assertEqual(result, "file transcription")
		payload_text = json.dumps(poster.last_payload)
		# The dedicated file prompt is sent as the system instruction.
		self.assertIn("audio transcription engine", payload_text)
		self.assertIn("audio file", payload_text)
		inline = poster.last_payload["contents"][0]["parts"][0][
			"inline_data"
		]
		self.assertEqual(inline["mime_type"], "audio/mpeg")
		self.assertEqual(
			base64.b64decode(inline["data"]),
			b"\x00\x01",
		)

	def test_transcribe_prompt_asks_for_line_breaks(self):
		poster = FakePoster([_success_body("ok")])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		client.transcribe(b"\x00\x01")
		payload_text = json.dumps(poster.last_payload)
		self.assertIn("line breaks", payload_text.lower())
		self.assertIn("own line", payload_text.lower())

	def test_empty_response_raises(self):
		poster = FakePoster(
			[(200, json.dumps({"candidates": []}).encode("utf-8"))]
		)
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		with self.assertRaises(gemini.AllKeysFailedError):
			client.refine("hello")

	def test_error_mapping_http_500(self):
		body = json.dumps(
			{"error": {"status": "INTERNAL", "message": "boom"}}
		).encode("utf-8")
		poster = FakePoster([(500, body)])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		with self.assertRaises(gemini.AllKeysFailedError) as ctx:
			client.refine("hello")
		self.assertEqual(ctx.exception.last_error.category, "server")

	def test_error_mapping_404(self):
		body = json.dumps(
			{"error": {"status": "NOT_FOUND", "message": "model not found"}}
		).encode("utf-8")
		poster = FakePoster([(404, body)])
		gemini._post_json = poster
		client = gemini.GeminiClient("key1", "flash")
		with self.assertRaises(gemini.AllKeysFailedError) as ctx:
			client.refine("hello")
		self.assertEqual(ctx.exception.last_error.category, "not_found")


class FakeGetter(object):
	"""Replaces gemini._get_json with scripted responses."""

	def __init__(self, responses):
		self.responses = list(responses)
		self.calls = []
		self.last_url = None

	def __call__(self, url, timeout=None):
		self.calls.append(url)
		self.last_url = url
		status, body = self.responses.pop(0)
		return status, body


def _models_body(*models):
	"""Build a models endpoint response from ``(name, methods)`` tuples."""
	entries = [
		{
			"name": "models/%s" % name,
			"supportedGenerationMethods": methods,
		}
		for name, methods in models
	]
	return json.dumps({"models": entries}).encode("utf-8")


class ListModelsTest(unittest.TestCase):

	def test_fetches_and_filters_models(self):
		getter = FakeGetter(
			[
				(
					200,
					_models_body(
						("gemini-3.7-flash", ["generateContent"]),
						("gemini-2.5-pro", ["generateContent"]),
						("gemini-3.1-flash-lite-image", ["generateImages"]),
						# Supports generateContent but is a TTS model: still
						# filtered out by its name.
						("gemini-2.5-flash-preview-tts", ["generateContent"]),
						("gemini-embedding-2-preview", ["embedContent"]),
					),
				),
			]
		)
		gemini._get_json = getter
		models = gemini.list_models("key1")
		self.assertIn("key=key1", getter.last_url)
		# Only generateContent text models survive; specialized ones are
		# filtered out and the known models come first.
		self.assertEqual(models, ["gemini-3.7-flash", "gemini-2.5-pro"])

	def test_no_keys_raises_immediately(self):
		with self.assertRaises(gemini.AllKeysFailedError) as ctx:
			gemini.list_models(" , ")
		self.assertIsNone(ctx.exception.last_error)

	def test_rotation_on_exhausted(self):
		exhausted = 429, json.dumps(
			{"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}
		).encode("utf-8")
		getter = FakeGetter(
			[
				exhausted,
				(
					200,
					_models_body(("gemini-3.6-flash", ["generateContent"])),
				),
			]
		)
		gemini._get_json = getter
		models = gemini.list_models("key1,key2")
		self.assertEqual(models, ["gemini-3.6-flash"])
		self.assertEqual(len(getter.calls), 2)
		self.assertIn("key=key1", getter.calls[0])
		self.assertIn("key=key2", getter.calls[1])

	def test_all_keys_failed_reports_last_error(self):
		exhausted = 429, json.dumps(
			{"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}
		).encode("utf-8")
		getter = FakeGetter([exhausted, exhausted])
		gemini._get_json = getter
		with self.assertRaises(gemini.AllKeysFailedError) as ctx:
			gemini.list_models("key1,key2")
		self.assertEqual(ctx.exception.last_error.category, "exhausted")
		self.assertEqual(len(getter.calls), 2)

	def test_http_error_raises(self):
		body = json.dumps(
			{"error": {"status": "API_KEY_INVALID", "message": "API key not valid."}}
		).encode("utf-8")
		getter = FakeGetter([(400, body)])
		gemini._get_json = getter
		with self.assertRaises(gemini.AllKeysFailedError) as ctx:
			gemini.list_models("key1")
		self.assertEqual(ctx.exception.last_error.category, "invalid_key")

	def test_unknown_models_appended_after_defaults(self):
		getter = FakeGetter(
			[
				(
					200,
					_models_body(
						("gemini-2.5-flash-lite", ["generateContent"]),
						("gemini-future-1", ["generateContent"]),
						("gemini-3.7-flash", ["generateContent"]),
					),
				),
			]
		)
		gemini._get_json = getter
		models = gemini.list_models("key1")
		# Known models first (in the default order), then unknown ones sorted.
		self.assertEqual(
			models,
			["gemini-3.7-flash", "gemini-2.5-flash-lite", "gemini-future-1"],
		)



if __name__ == "__main__":
	unittest.main()
