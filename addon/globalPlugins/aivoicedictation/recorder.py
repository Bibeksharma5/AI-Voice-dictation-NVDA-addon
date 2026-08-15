# -*- coding: UTF-8 -*-
# AI voice dictation - microphone recording using the Windows multimedia
# (winmm) waveIn API through ctypes.
#
# This uses only the Python standard library plus ctypes, so the add-on does
# not need to bundle any platform-specific binaries.
import ctypes
import io
import threading
import time
import wave
from ctypes import wintypes

#: DWORD_PTR (pointer-sized unsigned integer). Not provided by
#: ctypes.wintypes on all Python versions.
if ctypes.sizeof(ctypes.c_void_p) == 8:
	DWORD_PTR = ctypes.c_ulonglong
else:
	DWORD_PTR = ctypes.c_ulong

WAVE_FORMAT_PCM = 0x0001
CALLBACK_NULL = 0x00000000
WAVEIN_MAPPER = -1  # 0xFFFFFFFF as an unsigned value

WHDR_DONE = 0x00000001
WHDR_PREPARED = 0x00000002
WHDR_INQUEUE = 0x00000010

MMSYSERR_NOERROR = 0
MMSYSERR_BADDEVICEID = 2
MMSYSERR_ALLOCATED = 4
MMSYSERR_NODRIVER = 6

#: Recording format: 16 kHz, 16-bit, mono. Compact and well supported by the
#: Gemini API (1 minute of audio is about 1.9 MB).
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
#: Length of each audio buffer in seconds.
CHUNK_SECONDS = 0.5
#: Number of queued buffers. This also caps how long a recording can be
#: if the driver does not deliver WIM_DATA callbacks (each buffer holds
#: CHUNK_SECONDS of audio, so 20 buffers give 10 seconds of headroom).
NUM_BUFFERS = 20


class RecordingError(Exception):
	"""Raised when the microphone cannot be used."""


_winmm = None
if hasattr(ctypes, "WinDLL"):
	try:
		_winmm = ctypes.WinDLL("winmm.dll")
	except Exception:
		_winmm = None


class WAVEFORMATEX(ctypes.Structure):
	_fields_ = [
		("wFormatTag", wintypes.WORD),
		("nChannels", wintypes.WORD),
		("nSamplesPerSec", wintypes.DWORD),
		("nAvgBytesPerSec", wintypes.DWORD),
		("nBlockAlign", wintypes.WORD),
		("wBitsPerSample", wintypes.WORD),
		("cbSize", wintypes.WORD),
	]


class WAVEHDR(ctypes.Structure):
	_fields_ = [
		("lpData", wintypes.LPVOID),
		("dwBufferLength", wintypes.DWORD),
		("dwBytesRecorded", wintypes.DWORD),
		("dwUser", DWORD_PTR),
		("dwFlags", wintypes.DWORD),
		("dwLoops", wintypes.DWORD),
		("lpNext", wintypes.LPVOID),
		("reserved", DWORD_PTR),
	]


if _winmm is not None:
	WAVEINPROC = ctypes.WINFUNCTYPE(
		None,
		wintypes.HANDLE,  # hwi
		wintypes.UINT,  # uMsg
		DWORD_PTR,  # dwInstance
		DWORD_PTR,  # dwParam1
		DWORD_PTR,  # dwParam2
	)

	def _dummyCallback(hwi, uMsg, dwInstance, dwParam1, dwParam2):
		"""Never invoked (the device is opened with CALLBACK_NULL); this only
		keeps ctypes' argument type checking satisfied."""
		return None

	#: A never-invoked callback required by ctypes for the waveInOpen
	#: signature even though CALLBACK_NULL is used.
	_DUMMY_CALLBACK = WAVEINPROC(_dummyCallback)

	_waveInGetNumDevs = _winmm.waveInGetNumDevs
	_waveInGetNumDevs.restype = wintypes.UINT
	_waveInGetNumDevs.argtypes = []

	_waveInOpen = _winmm.waveInOpen
	_waveInOpen.restype = wintypes.DWORD
	_waveInOpen.argtypes = [
		ctypes.POINTER(wintypes.HANDLE),
		wintypes.UINT,
		ctypes.POINTER(WAVEFORMATEX),
		WAVEINPROC,
		DWORD_PTR,
		wintypes.DWORD,
	]

	_waveInPrepareHeader = _winmm.waveInPrepareHeader
	_waveInPrepareHeader.restype = wintypes.DWORD
	_waveInPrepareHeader.argtypes = [
		wintypes.HANDLE,
		ctypes.POINTER(WAVEHDR),
		wintypes.UINT,
	]

	_waveInAddBuffer = _winmm.waveInAddBuffer
	_waveInAddBuffer.restype = wintypes.DWORD
	_waveInAddBuffer.argtypes = [
		wintypes.HANDLE,
		ctypes.POINTER(WAVEHDR),
		wintypes.UINT,
	]

	_waveInStart = _winmm.waveInStart
	_waveInStart.restype = wintypes.DWORD
	_waveInStart.argtypes = [wintypes.HANDLE]

	_waveInStop = _winmm.waveInStop
	_waveInStop.restype = wintypes.DWORD
	_waveInStop.argtypes = [wintypes.HANDLE]

	_waveInReset = _winmm.waveInReset
	_waveInReset.restype = wintypes.DWORD
	_waveInReset.argtypes = [wintypes.HANDLE]

	_waveInUnprepareHeader = _winmm.waveInUnprepareHeader
	_waveInUnprepareHeader.restype = wintypes.DWORD
	_waveInUnprepareHeader.argtypes = [
		wintypes.HANDLE,
		ctypes.POINTER(WAVEHDR),
		wintypes.UINT,
	]

	_waveInClose = _winmm.waveInClose
	_waveInClose.restype = wintypes.DWORD
	_waveInClose.argtypes = [wintypes.HANDLE]


def trim_pcm(data, start_seconds=0.0, end_seconds=0.0):
	"""Remove audio from the start and end of raw PCM data.

	This is used to drop the sound of the keys pressed to start and stop
	the recording, which the microphone captures at the very beginning and
	end of the recording and which speech recognition can transcribe as
	unwanted extra characters (e.g. "00").

	:param data: Raw 16-bit PCM audio (mono, at :data:`SAMPLE_RATE`).
	:param start_seconds: Seconds of audio to drop from the start.
	:param end_seconds: Seconds of audio to drop from the end.
	"""
	frame_bytes = CHANNELS * SAMPLE_WIDTH
	remove_start = int(SAMPLE_RATE * start_seconds) * frame_bytes
	remove_end = int(SAMPLE_RATE * end_seconds) * frame_bytes
	if remove_start + remove_end >= len(data):
		# Nothing (or almost nothing) left; return the data unchanged
		# rather than producing a broken or empty recording.
		return data
	return data[remove_start : len(data) - remove_end]


def concatenate_wavs(chunks):
	"""Merge several WAV byte strings (all in the same format) into one.

	Used to join the separate parts of a dictation that was paused and
	resumed: each part is a complete WAV file and the parts are
	concatenated in order, producing a single WAV with the same format.

	:param chunks: List of WAV byte strings, in recording order.
	"""
	if len(chunks) == 1:
		return chunks[0]
	params = None
	frames = []
	for chunk in chunks:
		with wave.open(io.BytesIO(chunk), "rb") as wav_file:
			if params is None:
				params = wav_file.getparams()
			frames.append(wav_file.readframes(wav_file.getnframes()))
	output = io.BytesIO()
	with wave.open(output, "wb") as wav_file:
		wav_file.setparams(params)
		wav_file.writeframes(b"".join(frames))
	return output.getvalue()


def _error_message(mmr):
	"""Return a user friendly message for a waveIn error code."""
	if mmr == MMSYSERR_ALLOCATED:
		return (
			"The microphone is already in use by another application. "
			"Please close the application using it and try again."
		)
	if mmr == MMSYSERR_NODRIVER:
		return "No audio driver is installed on this system."
	if mmr == MMSYSERR_BADDEVICEID:
		return "No recording device was found."
	return "Unable to access the microphone (error %d)." % mmr


class WaveInRecorder(object):
	"""Records from the default microphone using the winmm waveIn API.

	The recording runs asynchronously (winmm calls the callback on its own
	thread); :meth:`start` returns immediately after the device starts.
	"""

	def __init__(self):
		self._handle = wintypes.HANDLE()
		self._headers = []  # list of (buffer, WAVEHDR)
		self._chunks = []
		self._open = False
		self._active = False
		self._stopPolling = threading.Event()
		self._pollThread = None

	# -- public API --------------------------------------------------------

	def start(self):
		"""Open the microphone and begin recording."""
		if _winmm is None:
			raise RecordingError(
				"Microphone recording is not supported on this system."
			)
		if self._active:
			return
		if _waveInGetNumDevs() == 0:
			raise RecordingError("No recording devices were found.")
		chunk_bytes = int(
			SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * CHUNK_SECONDS
		)
		fmt = WAVEFORMATEX(
			wFormatTag=WAVE_FORMAT_PCM,
			nChannels=CHANNELS,
			nSamplesPerSec=SAMPLE_RATE,
			nAvgBytesPerSec=SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH,
			nBlockAlign=CHANNELS * SAMPLE_WIDTH,
			wBitsPerSample=SAMPLE_WIDTH * 8,
			cbSize=0,
		)
		self._chunks = []
		self._stopPolling = threading.Event()
		err = _waveInOpen(
				ctypes.byref(self._handle),
				WAVEIN_MAPPER,
				ctypes.byref(fmt),
				_DUMMY_CALLBACK,
				0,
				CALLBACK_NULL,
			)
		if err != MMSYSERR_NOERROR:
			raise RecordingError(_error_message(err))
		self._open = True
		self._active = True
		try:
			for _ in range(NUM_BUFFERS):
				buffer = ctypes.create_string_buffer(chunk_bytes)
				header = WAVEHDR(
					lpData=ctypes.addressof(buffer),
					dwBufferLength=chunk_bytes,
					dwBytesRecorded=0,
					dwFlags=0,
				)
				self._headers.append((buffer, header))
				_require_success(
					_waveInPrepareHeader(
						self._handle,
					ctypes.byref(header),
					ctypes.sizeof(WAVEHDR),
				),					"Unable to prepare the microphone buffer.",
				)
				_waveInAddBuffer(
					self._handle,
					ctypes.byref(header),
					ctypes.sizeof(WAVEHDR),
				)
			# Start the device only after all buffers have been queued.
			# Calling waveInStart earlier (inside the loop) can make the
			# driver discard the buffers queued so far, truncating the
			# recording to a single buffer.
			err = _waveInStart(self._handle)
			if err != MMSYSERR_NOERROR:
				raise RecordingError(_error_message(err))
			self._pollThread = threading.Thread(
				target=self._pollLoop,
				name="aivoicedictation-recorder",
				daemon=True,
			)
			self._pollThread.start()
		except RecordingError:
			# Make sure the device is released if anything went wrong.
			self._cleanup()
			raise

	def stop_and_get_wav(self, trim_start=0.0, trim_end=0.0):
		"""Stop recording and return the recorded audio as WAV bytes.

		:param trim_start: Seconds of audio to drop from the start
			(e.g. the sound of the key pressed to start recording).
		:param trim_end: Seconds of audio to drop from the end
			(e.g. the sound of the key pressed to stop recording).
		"""
		self.stop()
		return self.get_wav_bytes(trim_start=trim_start, trim_end=trim_end)

	def stop(self):
		"""Stop recording. The recorded audio can still be obtained with
		:meth:`get_wav_bytes`."""
		if not self._open:
			return
		self._active = False
		self._stopPolling.set()
		if self._pollThread is not None:
			self._pollThread.join(timeout=1.0)
			self._pollThread = None
		_waveInStop(self._handle)
		_waveInReset(self._handle)
		# Collect any data that is still waiting in the buffers.
		self._harvestDoneBuffers(rearm=False)
		self._cleanup()

	def cancel(self):
		"""Stop recording and discard the recorded audio."""
		self._chunks = []
		self.stop()

	def get_wav_bytes(self, trim_start=0.0, trim_end=0.0):
		"""Return the recorded audio as a WAV file in memory.

		:param trim_start: Seconds of audio to drop from the start.
		:param trim_end: Seconds of audio to drop from the end.
		"""
		data = trim_pcm(
			b"".join(self._chunks), start_seconds=trim_start, end_seconds=trim_end
		)
		output = io.BytesIO()
		with wave.open(output, "wb") as wav_file:
			wav_file.setnchannels(CHANNELS)
			wav_file.setsampwidth(SAMPLE_WIDTH)
			wav_file.setframerate(SAMPLE_RATE)
			wav_file.writeframes(data)
		return output.getvalue()

	# -- internals ---------------------------------------------------------

	def _pollLoop(self):
		"""Recycle filled buffers while the device is recording.

		Some audio drivers never deliver the WIM_DATA callback that would
		normally be used to re-queue buffers. Without re-queuing, recording
		stops once every buffer in the pool has been filled, truncating
		longer dictations. This loop periodically checks the buffers and
		collects and re-queues any that are done, so recording continues
		for as long as needed.
		"""
		while not self._stopPolling.is_set():
			time.sleep(0.1)
			if self._active:
				self._harvestDoneBuffers()

	def _harvestDoneBuffers(self, rearm=True):
		"""Collect and (optionally) re-queue buffers that are full.

		A buffer is done when the driver has set WHDR_DONE and cleared
		WHDR_INQUEUE. Its recorded bytes are appended to the chunk list and
		it is handed back to the device unless C{rearm} is C{False} (used
		while stopping, after waveInReset has returned every buffer).
		"""
		for buffer, header in self._headers:
			if (header.dwFlags & WHDR_DONE) and not (
				header.dwFlags & WHDR_INQUEUE
			):
				if header.dwBytesRecorded:
					try:
						self._chunks.append(
							ctypes.string_at(
								buffer, header.dwBytesRecorded
							)
						)
					except Exception:
						pass
				header.dwBytesRecorded = 0
				header.dwFlags &= ~WHDR_DONE
				if rearm:
					try:
						_waveInAddBuffer(
							self._handle,
							ctypes.byref(header),
							ctypes.sizeof(WAVEHDR),
						)
					except Exception:
						pass

	def _cleanup(self):
		for buffer, header in self._headers:
			try:
				_waveInUnprepareHeader(
					self._handle,
					ctypes.byref(header),
					ctypes.sizeof(WAVEHDR),
				)
			except Exception:
				pass
		self._headers = []
		if self._open:
			try:
				_waveInClose(self._handle)
			except Exception:
				pass
			self._open = False


def _require_success(mmr, message):
	if mmr != MMSYSERR_NOERROR:
		raise RecordingError("%s %s" % (message, _error_message(mmr)))
