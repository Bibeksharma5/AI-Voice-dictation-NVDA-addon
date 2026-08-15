# -*- coding: UTF-8 -*-
# AI voice dictation - build variables for NVDA add-on builds.
import addonHandler

addon_info = addonHandler.AddonInfo()
addon_info.name = "aivoicedictation"
addon_info.summary = "AI voice dictation"
addon_info.description = (
	"AI voice dictation is a comprehensive toolkit for NVDA to dictate, translate "
	"and refine text. Refining involves correcting spelling and grammar, as well as "
	"formatting text with emojis."
)
addon_info.author = "Bibek Sharma Dhakal"
addon_info.url = ""
addon_info.version = "2026.1"
addon_info.updateChannel = None
addon_info.minimumNVDAVersion = "2026.1.0"
addon_info.lastTestedNVDAVersion = "2026.4.0"
