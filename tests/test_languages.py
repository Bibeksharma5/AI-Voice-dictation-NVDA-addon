# -*- coding: UTF-8 -*-
"""Unit tests for the translation language list."""
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

import languages  # noqa: E402


class LanguagesTest(unittest.TestCase):

	def test_language_count(self):
		self.assertGreaterEqual(len(languages.LANGUAGES), 100)

	def test_unique_names(self):
		names = [name for name, code in languages.LANGUAGES]
		self.assertEqual(len(names), len(set(names)))

	def test_unique_codes(self):
		codes = [code for name, code in languages.LANGUAGES]
		self.assertEqual(len(codes), len(set(codes)))

	def test_entries_well_formed(self):
		for name, code in languages.LANGUAGES:
			self.assertTrue(name)
			self.assertTrue(code)

	def test_default_language_present(self):
		names = [name for name, code in languages.LANGUAGES]
		self.assertIn(languages.DEFAULT_LANGUAGE, names)
		self.assertEqual(languages.DEFAULT_LANGUAGE, "English")


if __name__ == "__main__":
	unittest.main()
