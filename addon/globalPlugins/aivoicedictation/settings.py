# -*- coding: UTF-8 -*-
# AI voice dictation - configuration specification.
import config

#: The configuration section used by this add-on.
CONF_SECTION = "aivoicedictation"

#: The configuration spec, using NVDA's standard config spec syntax.
confspec = {
	"apiKeys": 'string(default="")',
	"showApi": "boolean(default=false)",
	"model": 'string(default="flash")',
	"translateAfterDictation": "boolean(default=false)",
	"targetLanguage": 'string(default="English")',
	"aiProcessing": "boolean(default=true)",
	"formatWithEmojis": "boolean(default=false)",
}

#: Default values, used as a fallback if a stored value cannot be read.
DEFAULTS = {
	"apiKeys": "",
	"showApi": False,
	"model": "flash",
	"translateAfterDictation": False,
	"targetLanguage": "English",
	"aiProcessing": True,
	"formatWithEmojis": False,
}


def register():
	"""Register the configuration spec with NVDA's config manager.

	Must be called before any add-on code reads or writes
	``config.conf[CONF_SECTION]``.
	"""
	if CONF_SECTION not in config.conf.spec:
		config.conf.spec[CONF_SECTION] = confspec


def get(key):
	"""Read a configuration value, falling back to the default."""
	try:
		value = config.conf[CONF_SECTION][key]
	except Exception:
		return DEFAULTS[key]
	if value is None:
		return DEFAULTS[key]
	return value
