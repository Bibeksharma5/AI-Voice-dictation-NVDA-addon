# -*- coding: UTF-8 -*-
# AI voice dictation - a global plugin for NVDA.
# Dictate, translate and refine text using Google Gemini.
#
# Commands:
#   NVDA+Alt+Space enters the AI voice dictation command layer. The next
#   single key press is interpreted as a command:
#     d - dictation (start/stop recording with the microphone)
#     b - transcribe an audio file selected in File Explorer
#     a - refine (correct spelling and grammar of) the clipboard text
#     e - format the clipboard text with emojis
#     t - translate the clipboard text
#     c - cancel the currently running operation
#     p - pause or resume dictation
#     enter - re-dictate the last recorded dictation (retry after a failure)
#     u - announce the current status (what operation is running)
#     i - open the AI voice dictation settings
#     h - open the help window listing all the commands
import os
import threading
import time

import addonHandler
import api
import globalPluginHandler
import inputCore
import queueHandler
import scriptHandler
import ui
import wx
from keyboardHandler import KeyboardInputGesture
from logHandler import log

addonHandler.initTranslation()

from . import gemini
from . import recorder
from . import settings as settingsModule
from .settings import CONF_SECTION, get as getSetting
from .settingsPanel import AIVoiceDictationSettingsPanel

try:
	settingsModule.register()
except Exception:
	# A failure here must not prevent the plugin itself from loading.
	log.exception(
		"Failed to register the AI voice dictation configuration spec"
	)

# The clipboard API was renamed in newer NVDA versions; support both names.
_getClipData = getattr(api, "getClipData", None) or getattr(
	api, "getClipboardData", None
)
_copyToClip = getattr(api, "copyToClip", None) or getattr(
	api, "copyToClipboard", None
)

#: Seconds of audio dropped from the start of the recording before it is
#: sent for transcription. This removes the sound of the key pressed to
#: start the recording, which the microphone captures at the very beginning.
TRIM_START_SECONDS = 0.15
#: Seconds of audio dropped from the end of the recording. This removes the
#: sound of the key pressed to stop the recording, which speech recognition
#: otherwise transcribes as unwanted extra characters (e.g. "00").
TRIM_END_SECONDS = 0.30


#: The text shown in the help window (the ``h`` command).
HELP_TEXT = _(
	"The following commands do the respective jobs after pressing AI "
	"voice dictation layered command (NVDA+Alt+Space):\n"
	"- A: Refines the clipboard text with AI that corrects the spelling "
	"and grammar\n"
	"- B: Transcribes the selected audio file from the file explorer\n"
	"- D: Starts and stops AI dictation\n"
	"- E: Formats the clipboard text with emojis\n"
	"- T: Translates the clipboard text into the target language selected "
	"in AI voice dictation in settings\n"
	"- Enter: Dictates the last dictation recording\n"
	"- C: Cancels any operation\n"
	"- U: Reports the status of any operation (eg: dictating, "
	"transcribing, refining etc)\n"
	"- I: Opens AI voice dictation settings\n"
	"- H: Displays this message"
)


class _TextViewerFrame(wx.Frame):
	"""Read-only frame that shows text with Copy and Close buttons.

	The frame provides Copy and Close buttons and closes when Escape is
	pressed (or the frame's close button is used). Alt+C copies the text.

	A plain read-only multiline text control is used rather than
	``wx.richtext.RichTextCtrl`` because NVDA's bundled wxPython does not
	include the ``wx.richtext`` module.
	"""

	def __init__(self, parent, title, text, copyMessage):
		super().__init__(parent, title=title, size=(700, 500))
		self._text = text
		self._copyMessage = copyMessage
		self._textCtrl = wx.TextCtrl(
			self,
			-1,
			text,
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.VSCROLL,
		)
		# Translators: Button label that copies the text to the clipboard.
		# The ampersand assigns the Alt+C accelerator.
		self._copyButton = wx.Button(self, label=_("&Copy"))
		# Translators: Button label that closes the text window.
		self._closeButton = wx.Button(self, label=_("Close"))
		buttonSizer = wx.BoxSizer(wx.HORIZONTAL)
		buttonSizer.Add(self._copyButton, 0, wx.ALL, 4)
		buttonSizer.Add(self._closeButton, 0, wx.ALL, 4)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self._textCtrl, 1, wx.EXPAND | wx.ALL, 8)
		sizer.Add(buttonSizer, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
		self.SetSizer(sizer)
		self.CentreOnScreen()
		self.Bind(wx.EVT_CHAR_HOOK, self._onKey)
		self.Bind(wx.EVT_BUTTON, self._onCopy, self._copyButton)
		self.Bind(wx.EVT_BUTTON, self._onClose, self._closeButton)
		self.Bind(wx.EVT_CLOSE, self._onClose)
		# The focused child controls, in tab order. The multiline text
		# control swallows Tab on Windows (native behavior of multiline
		# edit controls), so Tab/Shift+Tab are handled here explicitly.
		self._tabOrder = [self._textCtrl, self._copyButton, self._closeButton]

	def _onKey(self, event):
		keyCode = event.GetKeyCode()
		if keyCode == wx.WXK_ESCAPE:
			self.Close()
		elif keyCode == wx.WXK_TAB:
			self._moveFocus(forward=not event.ShiftDown())
		elif event.AltDown() and keyCode in (ord("C"), ord("c")):
			self._copyViaAltC()
		else:
			event.Skip()

	def _copyViaAltC(self):
		"""Handle the Alt+C shortcut.

		The ``&`` mnemonic in the Copy button label only works for dialogs
		(Windows processes button mnemonics in dialogs), so for this frame
		the shortcut is handled directly. Alt+C generates both a key-down
		and a character event; the time guard makes sure the copy runs only
		once.
		"""
		now = time.monotonic()
		if now - getattr(self, "_lastAltCPress", 0.0) > 0.5:
			self._lastAltCPress = now
			self._onCopy(None)

	def _moveFocus(self, forward):
		"""Move focus to the next/previous control in tab order."""
		current = self.FindFocus()
		try:
			index = self._tabOrder.index(current)
		except ValueError:
			# Focus is somewhere unexpected; start from the text control.
			index = 0
		if forward:
			index = (index + 1) % len(self._tabOrder)
		else:
			index = (index - 1) % len(self._tabOrder)
		self._tabOrder[index].SetFocus()

	def _onCopy(self, event):
		"""Copy the text to the clipboard."""
		if _copyToClip is not None and _copyToClip(self._text):
			ui.message(self._copyMessage)

	def _onClose(self, event=None):
		self.Destroy()


class TranscribedTextViewer(_TextViewerFrame):
	"""Read-only frame that shows the transcribed text of an audio file."""

	def __init__(self, parent, text):
		super().__init__(
			parent,
			# Translators: Title of the window showing the transcription of
			# a selected audio file.
			_("Transcribed text"),
			text,
			# Translators: Message announced after the transcribed text has
			# been copied to the clipboard.
			_("Transcription copied to clipboard."),
		)


class HelpViewer(_TextViewerFrame):
	"""Read-only frame that shows the AI voice dictation commands help."""

	def __init__(self, parent):
		super().__init__(
			parent,
			# Translators: Title of the window showing the AI voice
			# dictation commands help.
			_("AI voice dictation help"),
			HELP_TEXT,
			# Translators: Message announced after the help text has been
			# copied to the clipboard.
			_("Help text copied to clipboard."),
		)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	#: The category shown in NVDA's Input Gestures dialog.
	scriptCategory = _("AI voice dictation")

	def __init__(self):
		super().__init__()
		try:
			from gui.settingsDialogs import NVDASettingsDialog

			NVDASettingsDialog.categoryClasses.append(
				AIVoiceDictationSettingsPanel
			)
		except Exception:
			# The settings panel is optional; a failure here must not stop
			# the rest of the plugin from working.
			log.exception(
				"Failed to register the AI voice dictation settings panel"
			)
		self._layerActive = False
		self._recorder = None
		self._recording = False
		#: True while a dictation is being processed after recording stops,
		#: so a new recording cannot be started at the same time.
		self._busy = False
		#: The edit box focused when recording started; used as a fallback
		#: target when pasting the dictated text.
		self._dictationTarget = None
		#: The window showing a completed audio-file transcription, if one
		#: is open. Closed when the plugin terminates.
		self._transcriptionWindow = None
		#: The window showing the AI voice dictation commands help, if one
		#: is open. Closed when the plugin terminates.
		self._helpWindow = None
		#: The name of the currently running operation (e.g. "dictation",
		#: "refining"), or None when idle. Used by the cancel command.
		self._currentJob = None
		#: True while a cancel request for the current job is pending.
		#: Checked by background threads and the completion callbacks so a
		#: cancelled job never applies its result.
		self._cancelRequested = False
		#: Incremented for every new job. Results arriving from older jobs
		#: (cancelled or superseded) are discarded.
		self._jobId = 0
		#: The job id of the active dictation (recording + processing).
		self._dictationJobId = 0
		#: True while dictation is paused (recording stopped but the audio
		#: recorded so far is kept for later resumption).
		self._paused = False
		#: WAV chunks of the parts of the current dictation recorded before
		#: each pause. The parts are concatenated when dictation finishes.
		self._pendingWavChunks = []
		#: The finalized WAV bytes of the last completed dictation recording
		#: (after concatenation and trimming), or None if no dictation has
		#: been recorded yet. Kept so the user can re-run the dictation with
		#: the Enter command when it failed (e.g. because of a network or
		#: API problem) without recording again.
		self._lastDictationWav = None

	def terminate(self):
		self._exitCommandLayer()
		self._cancelRecording()
		if self._transcriptionWindow is not None:
			try:
				self._transcriptionWindow.Destroy()
			except Exception:
				pass
			self._transcriptionWindow = None
		if self._helpWindow is not None:
			try:
				self._helpWindow.Destroy()
			except Exception:
				pass
			self._helpWindow = None
		try:
			from gui.settingsDialogs import NVDASettingsDialog

			NVDASettingsDialog.categoryClasses.remove(
				AIVoiceDictationSettingsPanel
			)
		except (ValueError, AttributeError):
			pass

	# -- Command layer -----------------------------------------------------

	@scriptHandler.script(
		# Translators: Description of the command shown in NVDA's Input
		# Gestures dialog.
		description=_("Enter AI voice dictation command mode"),
		gesture="kb:NVDA+alt+space",
	)
	def script_enterCommandMode(self, gesture):
		if self._layerActive:
			return
		self._layerActive = True
		inputCore.manager._captureFunc = self._captureCommandKey
		# Translators: Message announced when entering the command layer.
		ui.message(
			_("Entering AI voice dictation command mode")
		)

	def _exitCommandLayer(self):
		self._layerActive = False
		try:
			if inputCore.manager._captureFunc == self._captureCommandKey:
				inputCore.manager._captureFunc = None
		except Exception:
			pass

	def _captureCommandKey(self, gesture):
		"""Handle the single key pressed inside the command layer.

		NVDA calls this from its input hook thread, so we only do minimal
		work here (read the key and leave the layer) and queue the actual
		command handling to the main thread, where speech is safe.
		"""
		# The layer consumes exactly one key press.
		self._exitCommandLayer()
		try:
			key = gesture.mainKeyName
		except Exception:
			key = None
		queueHandler.queueFunction(
			queueHandler.eventQueue, self._handleCommandKey, key
		)
		# Consume the gesture so it is not passed to the application.
		return False

	def _handleCommandKey(self, key):
		"""Run the command selected inside the layer (main thread)."""
		try:
			if key in (
				"a", "e", "t", "b", "i", "enter", "numpadEnter"
			) and (self._recording or self._paused):
				# A different command was requested during dictation
				# (recording or paused); discard it and run the new
				# command.
				self._cancelRecording()
			if key == "d":
				self._handleDictationCommand()
			elif key == "b":
				self._handleTranscribeFileCommand()
			elif key == "c":
				self._handleCancelCommand()
			elif key == "p":
				self._handlePauseCommand()
			elif key in ("enter", "numpadEnter"):
				self._handleRedictateCommand()
			elif key == "u":
				self._handleStatusCommand()
			elif key == "i":
				self._handleSettingsCommand()
			elif key == "h":
				self._handleHelpCommand()
			elif key == "a":
				self._handleRefineCommand()
			elif key == "e":
				self._handleEmojisCommand()
			elif key == "t":
				self._handleTranslateCommand()
			else:
				if self._recording or self._paused:
					self._cancelRecording()
				# Translators: Message announced for an unknown command key.
				ui.message(
					_("Not an AI voice dictation command, please try again.")
				)
		except Exception:
			log.exception("Error processing AI voice dictation command")

	def _startJob(self, name):
		"""Begin tracking a running job.

		:returns: The job identifier, which is passed to the background
			thread and checked when its result arrives, so results from
			cancelled or superseded jobs are discarded.
		"""
		self._currentJob = name
		self._cancelRequested = False
		self._jobId += 1
		return self._jobId

	def _finishJob(self, jobId):
		"""Clear the running job state when a job finishes.

		Called on the main thread when a job completes (with a result or
		with an error), so that a later status or cancel command does not
		report the finished job as still running.
		"""
		if jobId != self._jobId:
			# A newer job replaced this one; leave its state alone.
			return
		self._busy = False
		self._currentJob = None

	def _handleCancelCommand(self):
		"""Cancel the currently running operation, if any."""
		if self._recording or self._paused:
			# Cancel resets the whole dictation: the audio recorded so far
			# (including any paused parts) is discarded and dictation must
			# be started again from the beginning.
			jobName = "dictation"
			self._cancelRecording()
			self._currentJob = None
		elif self._currentJob is not None:
			jobName = self._currentJob
			self._cancelRequested = True
			self._busy = False
			self._currentJob = None
		else:
			# Translators: Message announced when the cancel command is
			# used while no operation is running.
			ui.message(_("No job to cancel."))
			return
		ui.message(_("%s cancelled.") % self._jobCancelLabel(jobName))

	@staticmethod
	def _jobCancelLabel(name):
		"""Return the human readable job name used in the cancel message."""
		labels = {
			"dictation": _("Dictation"),
			"transcription": _("Transcription"),
			"refining": _("Refining"),
			"emojis": _("Emoji formatting"),
			"translation": _("Translation"),
		}
		return labels.get(name, name)

	def _handleStatusCommand(self):
		"""Announce what operation is currently running."""
		if self._recording:
			# Translators: Status message announced while dictation is
			# being recorded.
			ui.message(_("Dictating..."))
		elif self._paused:
			# Translators: Status message announced while dictation is
			# paused.
			ui.message(_("Dictation paused."))
		elif self._currentJob is not None:
			ui.message(self._statusLabel(self._currentJob))
		else:
			# Translators: Status message announced when no operation is
			# running.
			ui.message(_("No operation running."))

	@staticmethod
	def _statusLabel(name):
		"""Return the status message for a running job name."""
		labels = {
			"dictation": _("Dictating..."),
			"transcription": _("Transcribing..."),
			"refining": _("Refining..."),
			"emojis": _("Formatting with emojis..."),
			"translation": _("Translating..."),
		}
		return labels.get(name, _("An operation is running."))

	def _handleHelpCommand(self):
		"""Open the window listing the AI voice dictation commands."""
		try:
			import gui

			if self._helpWindow is not None:
				# The help window is already open; bring it to the front. If
				# it was closed, any wx call on it raises, so fall through
				# and create a fresh one.
				try:
					self._helpWindow.Raise()
					self._helpWindow.SetFocus()
					return
				except Exception:
					self._helpWindow = None
			window = HelpViewer(gui.mainFrame)
		except Exception:
			log.exception(
				"Failed to create the AI voice dictation help window"
			)
			# Translators: Message announced when the help window cannot be
			# opened.
			ui.message(_("Unable to open the AI voice dictation help."))
			return
		self._helpWindow = window
		window.Show()
		window.Raise()

	def _handleSettingsCommand(self):
		"""Open NVDA's settings dialog at the AI voice dictation category."""
		try:
			import gui
			from gui.settingsDialogs import NVDASettingsDialog

			try:
				# NVDA 2023.2+ uses the public name without an underscore.
				popupSettingsDialog = gui.mainFrame.popupSettingsDialog
			except AttributeError:
				popupSettingsDialog = gui.mainFrame._popupSettingsDialog
			# The dialog must be created on the main thread. If it is
			# already open, NVDA focuses the existing instance.
			wx.CallAfter(
				popupSettingsDialog,
				NVDASettingsDialog,
				AIVoiceDictationSettingsPanel,
			)
		except Exception:
			log.exception("Failed to open the AI voice dictation settings")
			# Translators: Message announced when the settings dialog could
			# not be opened.
			ui.message(_("Unable to open the AI voice dictation settings."))

	# -- Dictation ---------------------------------------------------------

	def _handleDictationCommand(self):
		if self._recording or self._paused:
			self._stopDictationRecording()
		else:
			self._startDictationRecording()

	def _handlePauseCommand(self):
		"""Pause or resume the running dictation recording.

		While paused the microphone is stopped, so nothing is recorded;
		resuming starts a new recording whose audio is appended to the part
		recorded before the pause.
		"""
		if self._recording and not self._paused:
			micRecorder = self._recorder
			self._recorder = None
			try:
				wavBytes = micRecorder.stop_and_get_wav(
					trim_start=TRIM_START_SECONDS,
					trim_end=TRIM_END_SECONDS,
				)
			except Exception:
				self._cancelRecording()
				log.exception("Error pausing the recording")
				# Translators: Message announced when pausing fails.
				ui.message(_("Unable to pause the dictation."))
				return
			self._pendingWavChunks.append(wavBytes)
			self._recording = False
			self._paused = True
			# Translators: Message announced when dictation is paused.
			ui.message(_("Dictation paused."))
		elif self._paused:
			# Announce before starting the microphone so that the
			# announcement itself is not captured in the recording.
			# Translators: Message announced when dictation resumes.
			ui.message(_("Dictation resumed."))
			try:
				self._recorder = recorder.WaveInRecorder()
				self._recorder.start()
				self._recording = True
				self._paused = False
			except Exception:
				self._recorder = None
				log.exception(
					"Unable to resume the microphone recording"
				)
				# Translators: Message announced when resuming fails.
				ui.message(_("Unable to resume the dictation."))
				return
		else:
			# Translators: Message announced when the pause command is used
			# while no dictation is running.
			ui.message(_("Dictation not running."))

	def _handleRedictateCommand(self):
		"""Re-run the dictation pipeline on the last recorded dictation.

		If a dictation was recorded but failed (for example because of a
		network problem or an exhausted API key), this command sends the
		saved recording through the dictation pipeline again, so the user
		does not have to dictate the text again.
		"""
		if not self._hasApiKeys():
			self._announceMissingApiKey()
			return
		if self._busy:
			# Translators: Message announced when a new operation is
			# requested while the previous one is still processing.
			ui.message(
				_("Please wait, the previous operation is still processing.")
			)
			return
		if self._lastDictationWav is None:
			# Translators: Message announced when the re-dictate command is
			# used but no dictation recording has been saved yet.
			ui.message(_("No dictation found."))
			return
		# Check the edit box before doing any work: there is no point in
		# sending the recording to the API if there is nowhere to paste the
		# result. Announcing this upfront avoids wasting the user's time.
		focus = api.getFocusObject()
		if not self._isEditable(focus):
			# Translators: Message announced when re-dictation is requested
			# while the focus is not on an edit box.
			ui.message(_("Please go to any edit box for dictation"))
			return
		# Remember the edit box so the text can be pasted there even if the
		# focus changes while the recording is being processed.
		self._dictationTarget = focus
		# Translators: Message announced while the saved recording is being
		# sent for transcription.
		ui.message(_("Dictating text..."))
		self._busy = True
		jobId = self._startJob("dictation")
		threading.Thread(
			target=self._processDictation,
			args=(jobId, self._lastDictationWav),
			daemon=True,
		).start()

	def _startDictationRecording(self):
		if not self._hasApiKeys():
			self._announceMissingApiKey()
			return
		if self._busy:
			# Translators: Message announced when a new dictation is
			# requested while the previous one is still being processed.
			ui.message(
				_("Please wait, the previous dictation is still processing.")
			)
			return
		focus = api.getFocusObject()
		if not self._isEditable(focus):
			# Translators: Message announced when dictation is started while
			# the focus is not on an edit box.
			ui.message(_("Please go to any edit box for dictation"))
			return
		self._pendingWavChunks = []
		self._paused = False
		self._dictationTarget = focus
		# Translators: Message announced while listening for speech.
		ui.message(_("Listening..."))
		try:
			self._recorder = recorder.WaveInRecorder()
			self._recorder.start()
			self._recording = True
			self._dictationJobId = self._startJob("dictation")
		except recorder.RecordingError as e:
			self._recorder = None
			log.debugWarning("Unable to start recording: %s" % e)
			ui.message(str(e))
		except Exception:
			self._recorder = None
			log.exception("Unable to start the microphone recording")
			# Translators: Message announced when the microphone cannot be
			# started.
			ui.message(_("Unable to start the microphone recording."))

	def _stopDictationRecording(self):
		if not self._recording and not self._paused:
			return
		self._recording = False
		self._paused = False
		micRecorder = self._recorder
		self._recorder = None
		chunks = list(self._pendingWavChunks)
		self._pendingWavChunks = []
		if micRecorder is not None:
			# Stop the microphone first so that nothing announced afterwards
			# is captured in the recording.
			try:
				wavBytes = micRecorder.stop_and_get_wav(
					trim_start=TRIM_START_SECONDS,
					trim_end=TRIM_END_SECONDS,
				)
			except Exception:
				log.exception("Error stopping the recording")
				# Translators: Message announced when stopping the recording
				# fails.
				ui.message(_("Dictation failed while stopping the recording."))
				return
			chunks.append(wavBytes)
		if not chunks:
			# Nothing was recorded; do not start a job with empty audio.
			return
		# Join the parts recorded before and after each pause into a single
		# recording before sending it for transcription.
		wavBytes = recorder.concatenate_wavs(chunks)
		# Keep the finalized recording so the user can re-run this dictation
		# with the Enter command if it fails (e.g. because of a network or
		# API problem) without recording again.
		self._lastDictationWav = wavBytes
		# Translators: Message announced when the recording is being sent for
		# transcription.
		ui.message(_("Dictating text..."))
		self._busy = True
		threading.Thread(
			target=self._processDictation,
			args=(self._dictationJobId, wavBytes),
			daemon=True,
		).start()

	def _cancelRecording(self):
		if self._recorder is not None:
			try:
				self._recorder.cancel()
			except Exception:
				log.debugWarning(
					"Error cancelling the recording", exc_info=True
				)
			self._recorder = None
		self._recording = False
		self._paused = False
		self._pendingWavChunks = []

	def _processDictation(self, jobId, wavBytes):
		"""Run the dictation pipeline in a background thread.

		The result (or the error message) is handed back to the main
		thread when the pipeline finishes.
		"""
		text = None
		errorMessage = None
		try:
			client = self._makeClient()
			text = client.transcribe(wavBytes)
			if getSetting("translateAfterDictation") and not self._cancelRequested:
				# Translators: Message announced while translating.
				self._announce(_("Translating..."))
				text = client.translate(
					text, getSetting("targetLanguage")
				)
			if getSetting("aiProcessing") and not self._cancelRequested:
				# Translators: Message announced while correcting the text.
				self._announce(_("Correcting spelling and grammar..."))
				text = client.refine(text)
			if getSetting("formatWithEmojis") and not self._cancelRequested:
				# Translators: Message announced while formatting with
				# emojis.
				self._announce(_("Formatting with emojis..."))
				text = client.format_with_emojis(text)
		except gemini.AllKeysFailedError as e:
			errorMessage = self._errorMessage(e)
		except Exception:
			log.exception(
				"Unexpected error during dictation processing"
			)
			# Translators: Message announced when dictation processing fails
			# unexpectedly.
			errorMessage = _(
				"An unexpected error occurred during dictation."
			)
		wx.CallAfter(self._dictationFinished, jobId, text, errorMessage)

	def _dictationFinished(self, jobId, text, errorMessage):
		"""Complete the dictation on the main thread."""
		if jobId != self._jobId:
			# A newer job replaced this one; leave its state alone.
			return
		self._finishJob(jobId)
		if self._cancelRequested:
			self._cancelRequested = False
			return
		if errorMessage is not None:
			ui.message(errorMessage)
			return
		self._pasteDictatedText(text)

	def _pasteDictatedText(self, text):
		"""Paste the dictated text directly into an edit box (main thread).

		The text is pasted into the current edit box (or the edit box that
		was focused when recording started) and is also left on the
		clipboard, so it can be pasted again with Ctrl+V.
		"""
		target = None
		focus = api.getFocusObject()
		if self._isEditable(focus):
			target = focus
		elif self._dictationTarget is not None and self._isEditable(
			self._dictationTarget
		):
			target = self._dictationTarget
			try:
				target.setFocus()
			except Exception:
				target = None
		self._dictationTarget = None
		if target is None:
			# Translators: Message announced when the dictated text cannot
			# be pasted because the focus is not on an edit box.
			ui.message(_("Please go to any edit box for dictation"))
			return
		try:
			self._insertText(target, text)
		except Exception:
			log.exception("Failed to paste the dictated text")
			# Translators: Message announced when pasting fails.
			ui.message(_("Unable to paste the dictated text."))
			return
		# Translators: Message announced after the dictated text has been
		# pasted successfully.
		ui.message(_("Dictation completed successfully."))

	def _insertText(self, obj, text):
		"""Insert text at the caret of an edit box and leave it on the
		clipboard.

		The text is copied to the clipboard and pasted with the system
		paste command so that this works in every application. The clipboard
		keeps the dictated text, so it can be pasted again with Ctrl+V.
		"""
		self._copyToClipboard(text)
		try:
			KeyboardInputGesture.fromName("control+v").send()
		except Exception:
			raise

	# -- Audio file transcription -----------------------------------------

	def _handleTranscribeFileCommand(self):
		if not self._hasApiKeys():
			self._announceMissingApiKey()
			return
		if self._busy:
			# Translators: Message announced when a new operation is
			# requested while the previous one is still processing.
			ui.message(
				_("Please wait, the previous operation is still processing.")
			)
			return
		path = self._getSelectedFilePath()
		if path is None:
			# Translators: Message announced when the audio file
			# transcription command is used outside File Explorer or
			# without a file selected.
			ui.message(
				_(
					"Please focus on any audio file in file explorer and "
					"try again."
				)
			)
			return
		if not gemini.is_audio_file(path):
			# Translators: Message announced when the selected file is not
			# a supported audio type, so it is not uploaded to the API.
			ui.message(_("File not supported."))
			return
		# Translators: Message announced while the selected audio file is
		# being sent for transcription.
		ui.message(_("Transcribing audio..."))
		self._busy = True
		jobId = self._startJob("transcription")
		threading.Thread(
			target=self._transcribeAudioFile,
			args=(jobId, path),
			daemon=True,
		).start()

	def _transcribeAudioFile(self, jobId, path):
		"""Transcribe an audio file in a background thread."""
		text = None
		errorMessage = None
		try:
			with open(path, "rb") as f:
				audioBytes = f.read()
			client = self._makeClient()
			text = client.transcribe_file(
				audioBytes, gemini.audio_mime_type(path)
			)
		except gemini.AllKeysFailedError as e:
			errorMessage = self._errorMessage(e)
		except Exception:
			log.exception(
				"Unexpected error during audio file transcription"
			)
			# Translators: Message announced when audio file transcription
			# fails unexpectedly.
			errorMessage = _(
				"An unexpected error occurred while transcribing the audio."
			)
		wx.CallAfter(self._transcriptionFinished, jobId, text, errorMessage)

	def _transcriptionFinished(self, jobId, text, errorMessage):
		"""Complete the audio file transcription on the main thread."""
		if jobId != self._jobId:
			# A newer job replaced this one; leave its state alone.
			return
		self._finishJob(jobId)
		if self._cancelRequested:
			self._cancelRequested = False
			return
		if errorMessage is not None:
			ui.message(errorMessage)
			return
		try:
			import gui

			window = TranscribedTextViewer(gui.mainFrame, text)
		except Exception:
			log.exception(
				"Failed to create the transcribed text window"
			)
			# Translators: Message announced when the transcribed text
			# cannot be shown in a window.
			ui.message(
				_(
					"Transcription completed, but the text window could "
					"not be opened."
				)
			)
			return
		self._transcriptionWindow = window
		window.Show()
		window.Raise()

	def _getSelectedFilePath(self):
		"""Return the path of the file selected in File Explorer, or None.

		The command only works when the focus is a file item inside a File
		Explorer window. The path is read from the item's accessible value
		if available, otherwise from the Windows Shell (COM) by matching
		the focused item's name in the Explorer window's folder.
		"""
		focus = api.getFocusObject()
		if focus is None:
			return None
		try:
			appName = getattr(focus.appModule, "appName", "") or ""
		except Exception:
			appName = ""
		if appName != "explorer":
			return None
		try:
			import controlTypes

			if focus.role != controlTypes.Role.LISTITEM:
				# The focus is somewhere else in Explorer (e.g. the folder
				# tree or a toolbar), not on a file item.
				return None
		except Exception:
			pass
		try:
			import winUser

			rootHandle = winUser.getAncestor(
				focus.windowHandle, winUser.GA_ROOT
			)
			if winUser.getClassName(rootHandle) not in (
				"CabinetWClass",
				"ExplorerWClass",
			):
				# The focus is not inside a File Explorer window (for
				# example it may be an icon on the desktop).
				return None
		except Exception:
			pass
		path = None
		try:
			value = focus.value
			if isinstance(value, str) and value.strip():
				path = value.strip()
		except Exception:
			pass
		if not path:
			try:
				acc = focus.IAccessibleObject
				if acc is not None:
					value = acc.accValue(0)
					if isinstance(value, str) and value.strip():
						path = value.strip()
			except Exception:
				pass
		if not path:
			path = self._selectedPathFromShell(focus)
		if path and os.path.isfile(path):
			return path
		return None

	def _selectedPathFromShell(self, focus):
		"""Find the focused item's full path via the Windows Shell COM."""
		try:
			import comtypes.client

			import winUser

			rootHandle = winUser.getAncestor(
				focus.windowHandle, winUser.GA_ROOT
			)
			itemName = getattr(focus, "name", None)
			if not itemName:
				return None
			shell = comtypes.client.CreateObject("Shell.Application")
			for window in shell.Windows():
				try:
					hwnd = int(window.HWND)
				except Exception:
					continue
				if hwnd != rootHandle:
					continue
				folder = window.Document.Folder
				for item in folder.Items():
					if item.Name == itemName:
						return str(item.Path)
		except Exception:
			log.debugWarning(
				"Unable to resolve the Explorer selection via the Shell",
				exc_info=True,
			)
		return None

	# -- Clipboard commands ------------------------------------------------

	def _handleRefineCommand(self):
		if not self._hasApiKeys():
			self._announceMissingApiKey()
			return
		text, error = self._getClipboardText()
		if error:
			self._announceClipboardError()
			return
		if text is None:
			self._announceEmptyClipboard()
			return
		# Translators: Message announced while refining the clipboard text.
		ui.message(_("Processing clipboard text with AI for refinement..."))
		jobId = self._startJob("refining")
		threading.Thread(
			target=self._refineClipboardText,
			args=(jobId, text),
			daemon=True,
		).start()

	def _refineClipboardText(self, jobId, text):
		try:
			result = self._makeClient().refine(text)
		except gemini.AllKeysFailedError as e:
			self._announce(self._errorMessage(e))
			wx.CallAfter(self._finishJob, jobId)
			return
		except Exception:
			log.exception("Unexpected error while refining the text")
			# Translators: Message announced when refining fails
			# unexpectedly.
			self._announce(
				_("An unexpected error occurred while refining the text.")
			)
			wx.CallAfter(self._finishJob, jobId)
			return
		wx.CallAfter(
			self._copyResult,
			jobId,
			result,
			# Translators: Message announced when refinement is complete.
			_(
				"Refinement complete. "
				"Press Ctrl+V to paste the refined text."
			),
		)

	def _handleEmojisCommand(self):
		if not self._hasApiKeys():
			self._announceMissingApiKey()
			return
		text, error = self._getClipboardText()
		if error:
			self._announceClipboardError()
			return
		if text is None:
			self._announceEmptyClipboard()
			return
		# Translators: Message announced while formatting the clipboard text
		# with emojis.
		ui.message(_("Refining clipboard text with emojis..."))
		jobId = self._startJob("emojis")
		threading.Thread(
			target=self._formatClipboardWithEmojis,
			args=(jobId, text),
			daemon=True,
		).start()

	def _formatClipboardWithEmojis(self, jobId, text):
		try:
			result = self._makeClient().format_with_emojis(text)
		except gemini.AllKeysFailedError as e:
			self._announce(self._errorMessage(e))
			wx.CallAfter(self._finishJob, jobId)
			return
		except Exception:
			log.exception("Unexpected error while formatting with emojis")
			# Translators: Message announced when emoji formatting fails
			# unexpectedly.
			self._announce(
				_(
					"An unexpected error occurred while formatting with "
					"emojis."
				)
			)
			wx.CallAfter(self._finishJob, jobId)
			return
		wx.CallAfter(
			self._copyResult,
			jobId,
			result,
			# Translators: Message announced when emoji formatting is
			# complete.
			_(
				"Text successfully refined with emojis. "
				"Press Ctrl+V to paste the refined text."
			),
		)

	def _handleTranslateCommand(self):
		if not self._hasApiKeys():
			self._announceMissingApiKey()
			return
		text, error = self._getClipboardText()
		if error:
			self._announceClipboardError()
			return
		if text is None:
			self._announceEmptyClipboard()
			return
		# Translators: Message announced while translating the clipboard
		# text.
		ui.message(_("Translating clipboard text..."))
		jobId = self._startJob("translation")
		threading.Thread(
			target=self._translateClipboardText,
			args=(jobId, text),
			daemon=True,
		).start()

	def _translateClipboardText(self, jobId, text):
		targetLanguage = getSetting("targetLanguage")
		try:
			result = self._makeClient().translate(text, targetLanguage)
		except gemini.AllKeysFailedError as e:
			self._announce(self._errorMessage(e))
			wx.CallAfter(self._finishJob, jobId)
			return
		except Exception:
			log.exception("Unexpected error while translating the text")
			# Translators: Message announced when translation fails
			# unexpectedly.
			self._announce(
				_("An unexpected error occurred while translating the text.")
			)
			wx.CallAfter(self._finishJob, jobId)
			return
		wx.CallAfter(
			self._copyResult,
			jobId,
			result,
			# Translators: Message announced when translation is complete.
			_(
				"Translation completed successfully. "
				"Press Ctrl+V to paste the translated text."
			),
		)

	def _copyResult(self, jobId, text, message):
		"""Copy a result to the clipboard and announce the message.

		Results from cancelled or superseded jobs are discarded.
		"""
		if jobId != self._jobId:
			# A newer job replaced this one; leave its state alone.
			return
		self._finishJob(jobId)
		if self._cancelRequested:
			self._cancelRequested = False
			return
		try:
			self._copyToClipboard(text)
		except Exception:
			log.exception("Failed to copy the result to the clipboard")
			# Translators: Message announced when copying a result fails.
			ui.message(_("Unable to copy the result to the clipboard."))
			return
		ui.message(message)

	# -- Helpers -----------------------------------------------------------

	def _makeClient(self):
		return gemini.GeminiClient(
			getSetting("apiKeys"),
			getSetting("model"),
		)

	def _hasApiKeys(self):
		apiKeys = (getSetting("apiKeys") or "").strip()
		return any(key.strip() for key in apiKeys.split(","))

	def _announceMissingApiKey(self):
		# Translators: Message announced when no Gemini API key has been
		# entered.
		ui.message(
			_("Please enter the google gemini API key to get started.")
		)

	def _getClipboardText(self):
		"""Read the clipboard text.

		:returns: A tuple ``(text, error)``. ``text`` is ``None`` if the
			clipboard is empty; ``error`` is truthy if the clipboard could
			not be read.
		"""
		if _getClipData is None:
			return None, True
		try:
			text = _getClipData()
		except Exception:
			log.debugWarning(
				"Unable to read the clipboard", exc_info=True
			)
			return None, True
		if not (text or "").strip():
			return None, False
		return text, False

	def _announceEmptyClipboard(self):
		# Translators: Message announced when the clipboard is empty.
		ui.message(
			_(
				"There is no text on the clipboard. "
				"Please copy the text to be processed, and try again."
			)
		)

	def _announceClipboardError(self):
		# Translators: Message announced when the clipboard cannot be read.
		ui.message(_("Unable to read the clipboard. Please try again."))

	def _copyToClipboard(self, text):
		if _copyToClip is None:
			raise RuntimeError("Clipboard API unavailable")
		if not _copyToClip(text):
			raise RuntimeError("Clipboard copy failed")

	def _isEditable(self, obj):
		"""Return True if the object is an editable text field.

		The class check covers edit boxes, rich text editors, terminals and
		web form fields, including objects whose role is reported as a
		document (e.g. Notepad's editor).
		"""
		if obj is None:
			return False
		try:
			from editableText import EditableText

			if isinstance(obj, EditableText):
				return True
		except Exception:
			pass
		try:
			import controlTypes

			if (
				hasattr(controlTypes.State, "EDITABLE")
				and controlTypes.State.EDITABLE in obj.states
			):
				return True
			return obj.role in (
				controlTypes.Role.EDITABLETEXT,
				controlTypes.Role.MULTILINEEDIT,
				controlTypes.Role.TERMINAL,
			)
		except Exception:
			return False

	def _announce(self, message):
		"""Announce a message from a background thread."""
		try:
			wx.CallAfter(ui.message, message)
		except Exception:
			pass

	def _errorMessage(self, error):
		"""Return a user friendly message for an API failure."""
		category = getattr(error.last_error, "category", None)
		if category == "exhausted":
			# Translators: Message announced when all API keys are
			# exhausted.
			return _(
				"All Gemini API keys are exhausted. Please try again later."
			)
		if category == "invalid_key":
			# Translators: Message announced when the API key is invalid.
			return _(
				"The Gemini API key is invalid. "
				"Please check the API keys in NVDA settings."
			)
		if category == "permission":
			# Translators: Message announced when the API key does not have
			# permission for the requested model.
			return _(
				"The Gemini API key does not have permission to use the "
				"selected model."
			)
		if category in ("not_found", "model_error"):
			# Translators: Message announced when the selected model is not
			# available.
			return _(
				"The selected Gemini model is not available. "
				"Please try again later."
			)
		if category == "network":
			# Translators: Message announced when the Gemini API cannot be
			# reached.
			return _(
				"Unable to connect to the Gemini API. "
				"Please check your internet connection and try again."
			)
		if category == "server":
			# Translators: Message announced when the Gemini API is
			# temporarily unavailable.
			return _(
				"The Gemini API is temporarily unavailable. "
				"Please try again later."
			)
		# Translators: Message announced for an unknown Gemini API error.
		return _(
			"An error occurred while communicating with the Gemini API. "
			"Please try again."
		)
