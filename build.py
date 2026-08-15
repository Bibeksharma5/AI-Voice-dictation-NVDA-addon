#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""Build the AI voice dictation add-on package (.nvda-addon).

NVDA add-on bundles must have the manifest.ini and the add-on code at the
root of the archive: the ``addon/`` folder in this repository is only a
development layout, and its *contents* are placed at the archive root.

Run with:  python build.py
"""

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.join(ROOT, "addon")
MANIFEST_PATH = os.path.join(ROOT, "manifest.ini")

#: Files/folders that must never be packed into the bundle.
EXCLUDED_DIRS = ("__pycache__",)


def parse_manifest(path):
	"""Read name/value pairs from the flat manifest.ini file."""
	values = {}
	with open(path, "r", encoding="utf-8") as f:
		for line in f:
			line = line.strip()
			if not line or line.startswith("#") or "=" not in line:
				continue
			key, _, value = line.partition("=")
			values[key.strip()] = value.strip().strip('"')
	return values


def build():
	if not os.path.isfile(MANIFEST_PATH):
		sys.exit("manifest.ini not found in %s" % ROOT)
	if not os.path.isdir(ADDON_DIR):
		sys.exit("addon/ directory not found in %s" % ROOT)

	info = parse_manifest(MANIFEST_PATH)
	name = info.get("name", "aivoicedictation")
	version = info.get("version", "0.0.0")
	out_path = os.path.join(ROOT, "%s-%s.nvda-addon" % (name, version))

	with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
		# manifest.ini at the archive root.
		z.write(MANIFEST_PATH, "manifest.ini")
		# Contents of addon/ at the archive root (no "addon/" prefix).
		for dirpath, dirnames, filenames in os.walk(ADDON_DIR):
			dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
			for filename in filenames:
				if filename.endswith(".pyc"):
					continue
				full = os.path.join(dirpath, filename)
				relative = os.path.relpath(full, ADDON_DIR).replace(
					os.sep, "/"
				)
				z.write(full, relative)

	verify(out_path)
	print("Built: %s" % out_path)
	return out_path


def verify(out_path):
	"""Make sure the bundle has the layout NVDA expects."""
	with zipfile.ZipFile(out_path) as z:
		names = z.namelist()
	problems = []
	if "manifest.ini" not in names:
		problems.append("manifest.ini is missing from the archive root")
	if not any(n.startswith("globalPlugins/") for n in names):
		problems.append("globalPlugins/ is missing from the archive root")
	if any(n.startswith("addon/") for n in names):
		problems.append(
			"entries must not be prefixed with addon/ "
			"(NVDA looks for globalPlugins/ at the archive root)"
		)
	if problems:
		sys.exit("Invalid bundle:\n  " + "\n  ".join(problems))
	print(
		"Verified: manifest.ini and globalPlugins/ are at the archive root"
	)


if __name__ == "__main__":
	build()
