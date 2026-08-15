# AI voice dictation

AI voice dictation is a comprehensive, all-in-one voice toolkit for NVDA, powered
by Google Gemini, that turns your speech into polished text and supercharges
everyday productivity. Dictate naturally into any edit box, transcribe audio
files directly from File Explorer, refine clipboard text with professional
spelling and grammar correction, enrich your writing with tasteful emoji
formatting, and translate instantly into more than 100 languages.

Every feature is built around accessibility and efficiency: a single layered
command system (`NVDA+Alt+Space`) puts the entire toolkit at your fingertips,
multiple Gemini API keys are rotated automatically whenever one is exhausted,
dictation can be paused and resumed without losing your place, any running
operation can be cancelled with a single key, current status is always one
keystroke away, and the last dictation recording can be re-run if a network or
API failure interrupts it. Whether you are drafting documents, composing
messages, working with audio recordings, or communicating across languages, AI
voice dictation is your voice-powered productivity companion, made and
maintained in Nepal.

- **Author:** Bibek Sharma Dhakal
- **Version:** 2026.1
- **Minimum tested NVDA version:** 2026.1
- **Last tested NVDA version:** 2026.4

## Features

- Dictate directly into any edit box using your microphone and Google Gemini.
- Transcribe an audio file selected in File Explorer; the result opens in a
  read-only "Transcribed text" window with Copy (Alt+C) and Close buttons
  (Escape also closes it).
- Refine (correct spelling and grammar of) the text currently on the clipboard.
- Format clipboard text with emojis.
- Translate clipboard text into any of 100+ languages.
- Multiple Gemini API keys can be entered, separated by commas; if one key fails
  (for example because it is exhausted), the next key is tried automatically.

## Requirements

- NVDA 2026.1 or later.
- One or more Google Gemini API keys.
  - Create a key at https://aistudio.google.com/apikey (or the Google AI for
    Developers console).
  - The API keys are stored in your NVDA configuration and are hidden by default
    (see "Show API" below).

## Getting started

1. Install the add-on and restart NVDA.
2. Open NVDA Preferences > Settings and open the **AI voice dictation**
   category.
3. Paste your Gemini API key(s) into the **Gemini API keys (separatable with
   comma)** field. Multiple keys are separated with a comma, for example:
   `AIza...1111, AIza...2222`.
4. Choose a dictation model: **dictation lite** (Gemini 3.5 Flash Lite) or
   **dictation flash** (Gemini 3.5 Flash).
5. Optionally choose a target language and enable translation / AI processing /
   emoji formatting.
6. Press OK.

## Commands

The add-on adds one command in NVDA's Input Gestures dialog, under the
**AI voice dictation** category:

- **Enter AI voice dictation command mode** — default gesture: `NVDA+Alt+Space`

Pressing `NVDA+Alt+Space` makes NVDA announce "Entering AI voice dictation
command mode". The next single key press is then interpreted as a command:

| Key | Action |
| --- | --- |
| `d` | Dictation. If you are not on an edit box, NVDA announces "Please go to any edit box for dictation" and nothing is recorded. If you are on an edit box, recording starts immediately ("Listening..."). Press `NVDA+Alt+Space` followed by `d` again to stop recording and process the dictation. The recognized text is pasted directly into the current edit box and is also placed on the clipboard, so you can paste it elsewhere with Ctrl+V. |
| `b` | Transcribe an audio file. Focus a file (WAV, MP3, M4A, OGG, FLAC, AAC, etc.) in File Explorer and press `NVDA+Alt+Space` followed by `b`. NVDA announces "Transcribing audio..." and, when finished, opens a read-only window titled "Transcribed text" containing the transcription. Use the Copy button (or Alt+C) to copy the text to the clipboard (NVDA announces "Transcription copied to clipboard."), and the Close button or Escape (or the window's close button) to close the window. If the focus is not on a file in File Explorer, NVDA announces "Please focus on any audio file in file explorer and try again.". If a file is selected but it is not a supported audio type, NVDA announces "File not supported." and nothing is uploaded. |
| `a` | Refine the clipboard text (spelling and grammar). The refined text is copied back to the clipboard. |
| `e` | Format the clipboard text with emojis. The result is copied back to the clipboard. |
| `t` | Translate the clipboard text to the language selected in settings. The translation is copied back to the clipboard. |
| `c` | Cancel the currently running operation. NVDA announces the cancelled job (e.g. "Dictation cancelled.", "Transcription cancelled.", "Refining cancelled.", "Translation cancelled.", "Emoji formatting cancelled."). Cancelling a dictation discards the temporary audio, so dictation must be started again from the beginning. If nothing is running, NVDA announces "No job to cancel." |
| `p` | Pause or resume dictation. While dictation is recording, pressing `p` announces "Dictation paused." and the microphone stops (nothing is recorded while paused). Pressing `p` again announces "Dictation resumed." and recording continues; the audio after resuming is appended to the part recorded before the pause, and the whole thing is transcribed together when dictation finishes. If dictation is not running, NVDA announces "Dictation not running." |
| `Enter` | Re-dictate the last dictation. Every finished dictation recording is kept, so if a dictation fails (for example because of a network problem or an exhausted API key), pressing `NVDA+Alt+Space` followed by `Enter` sends the saved recording through the dictation pipeline again — no need to dictate again. The re-dictated text is pasted into the edit box and processed according to the settings checkboxes, just like a normal dictation. You must be on an edit box: otherwise NVDA announces "Please go to any edit box for dictation" immediately and nothing is sent to the API. If no dictation has been recorded yet, NVDA announces "No dictation found." |
| `u` | Announce the current status. While recording, NVDA announces "Dictating..."; while paused, "Dictation paused."; while an operation is being processed (dictation, transcription, refining, emoji formatting or translation), the corresponding status (e.g. "Transcribing...", "Refining...") is announced; otherwise "No operation running." |
| `i` | Open NVDA's settings dialog at the **AI voice dictation** category, ready to change API keys, model, language and processing options. If the settings dialog is already open, it is focused instead. |
| `h` | Open the **AI voice dictation help** window listing all the commands. The window has Copy (or Alt+C) and Close buttons, exactly like the "Transcribed text" window. |
| any other key | NVDA announces "Not an AI voice dictation command, please try again." |

## Settings

Available in NVDA Preferences > Settings > AI voice dictation:

- **Gemini API keys (separatable with comma):** the Google Gemini API key(s)
  used for all operations.
- **Show API:** when checked, the API key field is shown as plain text; when
  unchecked (default), it is masked like a password. Toggling this only changes
  whether the value is visible — the field stays in place and keeps its label.
- **Select dictation model:** *dictation lite* (Gemini 3.5 Flash Lite) or
  *dictation flash* (Gemini 3.5 Flash).
- **Translate after dictation finishes:** when checked, dictated text is
  translated to the selected target language. Unchecked by default.
- **Select target language:** the language used for translation (100+ languages).
- **AI processing after dictation completes:** when checked (default), the
  dictated text is corrected for spelling and grammar.
- **Format dictated text with emojis:** when checked, suitable emojis are added
  to the dictated text. Unchecked by default.

## Contributions are welcomed

We warmly welcome contributions from everyone, whether you are fixing a bug,
adding a new feature, improving documentation, or helping with translations.
Every contribution — no matter how small — helps make AI voice dictation better
for the whole community.

If you encounter any issue while using the add-on, please report it by opening an
issue on the project's GitHub repository. Include as much detail as you can — the
steps to reproduce the problem, any error messages you saw, and your NVDA version
— so that it can be resolved quickly. Thank you for helping us improve!

