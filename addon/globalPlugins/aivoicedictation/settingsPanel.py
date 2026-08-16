# -*- coding: UTF-8 -*-
# AI voice dictation - settings panel integrated into NVDA's settings dialog.
import ctypes
import threading

import addonHandler
import config
import gui
import wx
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from logHandler import log

from .gemini import (
	AllKeysFailedError,
	DEFAULT_MODELS,
	list_models,
	resolve_model,
)
from .languages import DEFAULT_LANGUAGE, LANGUAGES
from .settings import CONF_SECTION, get as getSetting

addonHandler.initTranslation()

#: Horizontal spacing between the label, the combo box and the fetch button
#: on the model row, matching NVDA's own settings dialog spacing.
try:
	_H_SPACE = guiHelper.SPACE_BETWEEN_HORIZONTAL_DIALOG_ITEMS
except AttributeError:
	_H_SPACE = 8

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

		# The model combo box lists real Gemini model IDs (the saved fetched
		# list, or the built-in defaults) with a "Fetch models" button next
		# to it. The label, combo box and button share one row.
		# Translators: Label for the dictation model combo box.
		modelLabel = wx.StaticText(self, label=_("Select dictation model:"))
		self.modelCombo = wx.Choice(self, choices=self._modelChoices())
		# Translators: Button that fetches the latest Gemini models from the
		# API and fills the model combo box.
		self.fetchModelsButton = wx.Button(
			self,
			label=_("Fetch models"),
		)
		self.fetchModelsButton.Bind(wx.EVT_BUTTON, self.onFetchModels)
		modelSizer = wx.BoxSizer(wx.HORIZONTAL)
		modelSizer.Add(modelLabel, 0, wx.ALIGN_CENTER_VERTICAL)
		modelSizer.AddSpacer(_H_SPACE)
		modelSizer.Add(self.modelCombo, 0, wx.ALIGN_CENTER_VERTICAL)
		modelSizer.AddSpacer(_H_SPACE)
		modelSizer.Add(
			self.fetchModelsButton,
			0,
			wx.ALIGN_CENTER_VERTICAL,
		)
		sHelper.addItem(modelSizer)
		self._selectSavedModel()

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

	def _savedModels(self):
		"""Model IDs saved from a previous fetch, or an empty list."""
		value = getSetting("modelsList") or ""
		return [model.strip() for model in value.split(",") if model.strip()]

	def _modelChoices(self):
		"""Choices for the model combo box.

		The models fetched (and saved) earlier win; otherwise the built-in
		list of the latest models is used.
		"""
		saved = self._savedModels()
		if saved:
			return saved
		return list(DEFAULT_MODELS)

	def _selectSavedModel(self):
		"""Select the saved model in the combo box."""
		model = resolve_model(getSetting("model"))
		choices = self.modelCombo.GetItems()
		if model in choices:
			self.modelCombo.SetSelection(choices.index(model))
		else:
			self.modelCombo.SetSelection(0)

	def onFetchModels(self, evt):
		"""Fetch the latest Gemini models in the background.

		The keys currently typed in the API field are used (they do not need
		to be saved yet). The combo box is filled from the background thread
		via ``wx.CallAfter`` so the settings dialog never freezes.
		"""
		apiKeys = self._apiKeysEdit.GetValue().strip()
		if not any(key.strip() for key in apiKeys.split(",")):
			# Translators: Message shown when trying to fetch models without
			# any API key.
			gui.messageBox(
				_("Please enter the google gemini API key to get started."),
				_("AI voice dictation"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		self.fetchModelsButton.Disable()
		# Translators: Label of the fetch button while models are being
		# fetched.
		self.fetchModelsButton.SetLabel(_("Fetching..."))
		threading.Thread(
			target=self._fetchWorker,
			args=(apiKeys,),
			daemon=True,
		).start()

	def _fetchWorker(self, apiKeys):
		"""Run the API request on a background thread."""
		try:
			models = list_models(apiKeys)
		except AllKeysFailedError as error:
			wx.CallAfter(self._fetchFailed, error)
			return
		wx.CallAfter(self._fetchSucceeded, models)

	def _fetchSucceeded(self, models):
		"""Fill the combo box with the fetched models."""
		self.fetchModelsButton.Enable()
		self.fetchModelsButton.SetLabel(_("Fetch models"))
		if not models:
			# Translators: Message shown when the API returned no usable
			# models.
			gui.messageBox(
				_("No usable models were found for the given API keys."),
				_("AI voice dictation"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		current = self.modelCombo.GetStringSelection()
		self.modelCombo.SetItems(models)
		if current in models:
			self.modelCombo.SetSelection(models.index(current))
		else:
			self.modelCombo.SetSelection(0)
		# Translators: Message shown after models are fetched successfully.
		# {count} is replaced with the number of fetched models.
		gui.messageBox(
			_("Fetched {count} models.").format(count=len(models)),
			_("AI voice dictation"),
			wx.OK | wx.ICON_INFORMATION,
			self,
		)

	def _fetchFailed(self, error):
		"""Restore the button and explain why fetching failed."""
		self.fetchModelsButton.Enable()
		self.fetchModelsButton.SetLabel(_("Fetch models"))
		gui.messageBox(
			self._fetchErrorMessage(error),
			_("AI voice dictation"),
			wx.OK | wx.ICON_ERROR,
			self,
		)

	@staticmethod
	def _fetchErrorMessage(error):
		"""User friendly message for a failed model fetch."""
		category = getattr(error.last_error, "category", None)
		if category == "exhausted":
			# Translators: Message shown when all API keys are exhausted
			# while fetching models.
			return _(
				"All Gemini API keys are exhausted. Please try again later."
			)
		if category == "invalid_key":
			# Translators: Message shown when the API key is invalid while
			# fetching models.
			return _(
				"The Gemini API key is invalid. "
				"Please check the API keys in NVDA settings."
			)
		if category == "network":
			# Translators: Message shown when the Gemini API cannot be
			# reached while fetching models.
			return _(
				"Unable to connect to the Gemini API. "
				"Please check your internet connection and try again."
			)
		# Translators: Generic message shown when fetching models fails.
		return _(
			"Unable to fetch the Gemini models. Please try again later."
		)

	def onSave(self):
		config.conf[CONF_SECTION]["apiKeys"] = self._apiKeysEdit.GetValue().strip()
		config.conf[CONF_SECTION]["showApi"] = self.showApiCheckBox.IsChecked()
		config.conf[CONF_SECTION]["model"] = (
			self.modelCombo.GetStringSelection()
		)
		config.conf[CONF_SECTION]["modelsList"] = ",".join(
			self.modelCombo.GetItems()
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
