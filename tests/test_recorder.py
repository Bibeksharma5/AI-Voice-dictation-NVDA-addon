# -*- coding: UTF-8 -*-
"""Unit tests for the microphone recorder module."""
import ctypes
import io
import os
import struct
import sys
import unittest
import wave

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

import recorder  # noqa: E402


class RecorderTest(unittest.TestCase):

	def test_wav_bytes_valid(self):
		rec = recorder.WaveInRecorder.__new__(recorder.WaveInRecorder)
		# 0.1 seconds of silence at the configured format.
		samples = recorder.SAMPLE_RATE // 10
		rec._chunks = [b"\x00\x00" * samples]
		data = rec.get_wav_bytes()
		self.assertEqual(data[:4], b"RIFF")
		self.assertEqual(data[8:12], b"WAVE")
		# fmt chunk: PCM, mono, 16 kHz, 16-bit.
		self.assertEqual(data[12:16], b"fmt ")
		format_tag, channels, rate = struct.unpack("<HHI", data[20:28])
		self.assertEqual(format_tag, 1)
		self.assertEqual(channels, recorder.CHANNELS)
		self.assertEqual(rate, recorder.SAMPLE_RATE)
		# data chunk length matches the payload.
		data_size = struct.unpack("<I", data[40:44])[0]
		self.assertEqual(data_size, samples * 2)
		self.assertEqual(data_size, len(data) - 44)

	def test_wav_bytes_empty(self):
		rec = recorder.WaveInRecorder.__new__(recorder.WaveInRecorder)
		rec._chunks = []
		data = rec.get_wav_bytes()
		self.assertEqual(data[:4], b"RIFF")
		self.assertEqual(data[8:12], b"WAVE")

	def test_start_raises_when_winmm_unavailable(self):
		original = recorder._winmm
		try:
			recorder._winmm = None
			rec = recorder.WaveInRecorder()
			with self.assertRaises(recorder.RecordingError):
				rec.start()
		finally:
			recorder._winmm = original

	def test_cancel_discards_chunks(self):
		rec = recorder.WaveInRecorder.__new__(recorder.WaveInRecorder)
		rec._chunks = [b"abc"]
		rec._open = False
		rec.cancel()
		self.assertEqual(rec.get_wav_bytes()[44:], b"")

	def test_trim_pcm_removes_head_and_tail(self):
		frame_bytes = recorder.CHANNELS * recorder.SAMPLE_WIDTH
		# 1 second of silence at the configured format, wrapped in marker
		# bytes that must be removed by the trim.
		silence = b"\x00\x00" * recorder.SAMPLE_RATE
		data = b"AAA" + silence + b"BBB"
		trimmed = recorder.trim_pcm(data, start_seconds=0.25, end_seconds=0.25)
		remove = int(recorder.SAMPLE_RATE * 0.25) * frame_bytes
		self.assertEqual(trimmed, data[remove : len(data) - remove])
		# No "AAA"/"BBB" marker bytes survive the trim.
		self.assertNotIn(b"AAA", trimmed)
		self.assertNotIn(b"BBB", trimmed)
		self.assertEqual(len(trimmed), len(data) - remove * 2)

	def test_trim_pcm_keeps_data_when_too_short(self):
		data = b"\x00\x00" * 100
		self.assertEqual(recorder.trim_pcm(data, start_seconds=5, end_seconds=5), data)

	def test_concatenate_wavs_joins_parts(self):
		frames1 = b"\x00\x00" * 100
		frames2 = b"\x11\x22" * 50
		chunks = []
		for frames in (frames1, frames2):
			output = io.BytesIO()
			with wave.open(output, "wb") as wav_file:
				wav_file.setnchannels(recorder.CHANNELS)
				wav_file.setsampwidth(recorder.SAMPLE_WIDTH)
				wav_file.setframerate(recorder.SAMPLE_RATE)
				wav_file.writeframes(frames)
			chunks.append(output.getvalue())
		merged = recorder.concatenate_wavs(chunks)
		self.assertEqual(merged[:4], b"RIFF")
		self.assertEqual(merged[8:12], b"WAVE")
		data_size = struct.unpack("<I", merged[40:44])[0]
		self.assertEqual(data_size, (100 + 50) * 2)
		self.assertEqual(data_size, len(merged) - 44)
		# The merged audio contains both parts, in order.
		with wave.open(io.BytesIO(merged), "rb") as wav_file:
			self.assertEqual(wav_file.readframes(200), frames1 + frames2)

	def test_concatenate_wavs_single_chunk_passthrough(self):
		output = io.BytesIO()
		with wave.open(output, "wb") as wav_file:
			wav_file.setnchannels(recorder.CHANNELS)
			wav_file.setsampwidth(recorder.SAMPLE_WIDTH)
			wav_file.setframerate(recorder.SAMPLE_RATE)
			wav_file.writeframes(b"\x00\x00" * 10)
		chunk = output.getvalue()
		self.assertEqual(recorder.concatenate_wavs([chunk]), chunk)

	def test_get_wav_bytes_with_trim(self):
		rec = recorder.WaveInRecorder.__new__(recorder.WaveInRecorder)
		# 1 second of audio; trim 0.25 s from both ends.
		rec._chunks = [b"\x00\x00" * recorder.SAMPLE_RATE]
		data = rec.get_wav_bytes(trim_start=0.25, trim_end=0.25)
		data_size = struct.unpack("<I", data[40:44])[0]
		expected_frames = recorder.SAMPLE_RATE - int(0.25 * recorder.SAMPLE_RATE) * 2
		self.assertEqual(data_size, expected_frames * 2)
		self.assertEqual(data_size, len(data) - 44)

	def test_harvest_done_buffers(self):
		rec = recorder.WaveInRecorder.__new__(recorder.WaveInRecorder)
		rec._chunks = []
		rec._handle = None
		# Two done buffers with data and one still in the queue.
		buf1 = b"\x00\x00" * 10
		hdr1 = recorder.WAVEHDR(
			dwBytesRecorded=20,
			dwFlags=recorder.WHDR_DONE,
		)
		buf2 = b"\x11\x22" * 10
		hdr2 = recorder.WAVEHDR(
			dwBytesRecorded=20,
			dwFlags=recorder.WHDR_DONE,
		)
		hdr3 = recorder.WAVEHDR(
			dwBytesRecorded=20,
			dwFlags=recorder.WHDR_INQUEUE,
		)
		rec._headers = [
			(ctypes.create_string_buffer(buf1), hdr1),
			(ctypes.create_string_buffer(buf2), hdr2),
			(ctypes.create_string_buffer(b"\x00" * 20), hdr3),
		]
		rec._harvestDoneBuffers(rearm=False)
		self.assertEqual(b"".join(rec._chunks), buf1 + buf2)
		# The in-queue buffer must be left alone.
		self.assertEqual(hdr3.dwBytesRecorded, 20)

	def test_harvest_done_buffers_ignores_requeued(self):
		rec = recorder.WaveInRecorder.__new__(recorder.WaveInRecorder)
		rec._chunks = []
		rec._handle = None
		# A done buffer must not be collected twice after its flag is cleared.
		hdr = recorder.WAVEHDR(
			dwBytesRecorded=20,
			dwFlags=recorder.WHDR_DONE,
		)
		rec._headers = [(ctypes.create_string_buffer(b"\x00\x00" * 10), hdr)]
		rec._harvestDoneBuffers(rearm=False)
		first = len(rec._chunks)
		rec._harvestDoneBuffers(rearm=False)
		self.assertEqual(len(rec._chunks), first)


if __name__ == "__main__":
	unittest.main()
