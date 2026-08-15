# -*- coding: UTF-8 -*-
# AI voice dictation - settings panel integrated into NVDA's settings dialog.
import ctypes

import addonHandler
import config
import wx
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from logHandler import log

from .languages import DEFAULT_LANGUAGE, LANGUAGES
from .settings import CONF_SECTION, get as getSetting

addonHandler.initTranslation()

# Translators: A model option in AI voice dictation settings.
MODEL_CHOICES = [
	_("dictation lite"),
	# Translators: A model option in AI voice dictation settings.
	_("dictation flash"),
]

_LANGUAGE_NAMES = [name for name, code in LANGUAGES]

#: Win32 message that sets (or removes) the password character of an edit
#: control. Using it lets us toggle the masking of the API keys field without
#: destroying and recreating the wx control (which would move the field and
#: break its label association).
EM_SETPASSWORDCHAR = 0x00CC


def _applyPasswordMask(ctrl, masked):
	"""Show or hide the contents of a wx.TextCtrl in place.

	:param ctrl: The text control whose contents should be masked.
	:param masked: C{True} to display the value as a password, C{False} to
		display it as plain text.
	"""
	try:
		hwnd = ctrl.GetHandle()
		if not hwnd:
			return
		passwordChar = ord("\u25CF") if masked else 0
		ctypes.windll.user32.SendMessageW(
			hwnd, EM_SETPASSWORDCHAR, passwordChar, 0
		)
		ctrl.Refresh()
	except Exception:
		log.exception("Unable to toggle the API key field masking")


class AIVoiceDictationSettingsPanel(SettingsPanel):
	# Translators: Title of the AI voice dictation settings category.
	title = _("AI voice dictation")

	def makeSettings(self, settingsSizer):
		sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		# Translators: Label for the Gemini API keys edit box.
		self._apiKeysEdit = sHelper.addLabeledControl(
			_("Gemini API keys (separatable with comma)"),
			wx.TextCtrl,
		)
		self._apiKeysEdit.SetValue(getSetting("apiKeys"))

		# Translators: Checkbox that controls whether the API keys are shown
		# as plain text or masked like a password.
		self.showApiCheckBox = wx.CheckBox(self, label=_("Show API"))
		self.showApiCheckBox.SetValue(getSetting("showApi"))
		self.showApiCheckBox.Bind(wx.EVT_CHECKBOX, self.onShowApiToggle)
		sHelper.addItem(self.showApiCheckBox)

		# Mask the API key field initially if "Show API" is unchecked. The
		# field itself is never recreated, so its position and label never
		# change.
		_applyPasswordMask(
			self._apiKeysEdit, not self.showApiCheckBox.IsChecked()
		)

		# Translators: Label for the dictation model combo box.
		self.modelCombo = sHelper.addLabeledControl(
			_("Select dictation model:"),
			wx.Choice,
			choices=MODEL_CHOICES,
		)
		model = getSetting("model")
		self.modelCombo.SetSelection(1 if model == "flash" else 0)

		# Translators: Checkbox to translate the dictated text after
		# transcription.
		self.translateCheckBox = wx.CheckBox(
			self,
			label=_("Translate after dictation finishes"),
		)
		self.translateCheckBox.SetValue(
			getSetting("translateAfterDictation")
		)
		sHelper.addItem(self.translateCheckBox)

		# Translators: Label for the target language combo box.
		self.languageCombo = sHelper.addLabeledControl(
			_("Select target language"),
			wx.Choice,
			choices=_LANGUAGE_NAMES,
		)
		targetLanguage = getSetting("targetLanguage")
		try:
			index = _LANGUAGE_NAMES.index(targetLanguage)
		except ValueError:
			index = _LANGUAGE_NAMES.index(DEFAULT_LANGUAGE)
		self.languageCombo.SetSelection(index)

		# Translators: Checkbox for AI post-processing (spelling and grammar)
		# of the dictated text.
		self.aiProcessingCheckBox = wx.CheckBox(
			self,
			label=_("AI processing after dictation completes"),
		)
		self.aiProcessingCheckBox.SetValue(
			getSetting("aiProcessing")
		)
		sHelper.addItem(self.aiProcessingCheckBox)

		# Translators: Checkbox to format the dictated text with emojis.
		self.emojisCheckBox = wx.CheckBox(
			self,
			label=_("Format dictated text with emojis"),
		)
		self.emojisCheckBox.SetValue(
			getSetting("formatWithEmojis")
		)
		sHelper.addItem(self.emojisCheckBox)

	def onShowApiToggle(self, evt):
		"""Mask or reveal the API keys in place; the field never moves."""
		_applyPasswordMask(self._apiKeysEdit, not evt.IsChecked())

	def onSave(self):
		config.conf[CONF_SECTION]["apiKeys"] = self._apiKeysEdit.GetValue().strip()
		config.conf[CONF_SECTION]["showApi"] = self.showApiCheckBox.IsChecked()
		config.conf[CONF_SECTION]["model"] = (
			"flash" if self.modelCombo.GetSelection() == 1 else "lite"
		)
		config.conf[CONF_SECTION]["translateAfterDictation"] = (
			self.translateCheckBox.IsChecked()
		)
		config.conf[CONF_SECTION]["targetLanguage"] = (
			self.languageCombo.GetStringSelection()
		)
		config.conf[CONF_SECTION]["aiProcessing"] = (
			self.aiProcessingCheckBox.IsChecked()
		)
		config.conf[CONF_SECTION]["formatWithEmojis"] = (
			self.emojisCheckBox.IsChecked()
		)
