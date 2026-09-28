[🇻🇳 Tiếng Việt](README.md) · **🇬🇧 English**

# Idea Note

A Windows app for quick notes, instead of sending notes to yourself on Zalo or Facebook: text and images, grouped by project, to-dos, urgent tasks, reminders, and copying context to Claude. It comes with a **phone version** (open in the browser, scan a QR code) and a **knowledge base you can ask with AI**, which can even search text inside screenshots.

**Private by design:** all data stays on your machine, no account, no author server, no analytics. See [SECURITY.md](SECURITY.md).

## Install on your computer (one install also gives you the phone version)
1. Go to this repo's **Releases**, download `IdeaNote-v…-win64.zip`.
2. Extract it to a folder of your own, for example `D:\IdeaNote` (not inside `Program Files`, the app writes data next to its executable).
3. Double-click `IdeaNote.exe`. If Windows SmartScreen asks, click **More info** then **Run anyway** (self-built app, not code signed yet).
4. To start with Windows so reminders always run: Settings, tick "Run in background at startup".

Data lives in the `data\` folder next to `IdeaNote.exe` (notes in `notes.json`, images in `img\`, daily backups in `backup\`, 30 kept). To upgrade: extract the new version over the old one, **keep the `data\` folder**.

## Use on your phone
Nothing extra to install: the desktop app has a small server on port 41900.
1. Put your phone on the **same Wi-Fi** as the computer.
2. In the app click **Phone**, scan the QR code with the camera or Zalo.
3. The first time, the browser says "Connection is not private" (the certificate is made by your own computer): **Advanced**, then **Proceed**.
4. Choose **Add to Home screen** to get an app icon.
5. Phone cannot connect: in the Phone dialog click **Open firewall for phone** (needs one Admin Yes, only opens it for Private networks).

The computer must be on and Idea Note running (minimized to the tray is fine) for the phone to connect.

## Usage
- Type in the top box, **Ctrl+Enter** saves. **Ctrl+V** pastes images, or drag and drop them in. The "Project" box groups notes by project.
- Middle list: one line per note, **↑ ↓** to move, click the circle to mark done, **Delete** deletes (to the trash).
- Right panel: edit directly (auto-saved) text, status, urgent, project, date, reminder time, progress, images.
- The window shrinks down to about 600×450: when narrow, the left column hides (click **☰** to show it).
- **Copy for Claude** (Ctrl+Shift+C): copies the current list with image paths. **Export pack**: a folder with CONTEXT.md and images.
- **Ctrl+Alt+N** anywhere: opens the app to write right away.

### Search (Ctrl+F)
- Results show as you type. With several words, a note must contain all of them (any order, Vietnamese accents optional).
- Results are grouped: title matches, content matches, text-in-image matches. Next to the title is the first matching line, lines from images are marked 📝.

### Image viewer
Click any image to open the full-screen viewer:
- **← →** (or swipe on a phone) to switch images, the thumbnail strip below jumps quickly.
- **Mouse wheel** or pinch to zoom at the exact spot, drag to pan, **double-click** to fit or 100%.
- **Text in image** (key T): view and copy the text the machine read from the image.

### Knowledge base: Ask (Ctrl+K)
Idea Note is both a task list and a place to store knowledge (slide photos, screenshots, class notes).
- **Reads text in every image** in the background. Search finds text inside images too.
- Click **Ask knowledge** and ask in plain words, for example "what do I know about CoS?". The AI answers briefly, **each point cites its source [1] [2]**; below are source cards: which note, which project, which date, which image. Click a card to open that note, click an image to open that exact image.
- Choose scope "All" or one project. Semantic search understands abbreviations (CoS is Chief of Staff) and questions that do not share words with the notes.

Tips for storing knowledge so you can ask it back accurately:
1. Put each learning source in **one project**, one note per session.
2. Write a clear **Title**, for example "Session 8: a 24/7 agent team".
3. Put many slide photos into **the same note** for that session; add 1 to 3 lines summarizing the key ideas yourself (the AI prefers what you wrote).

Requirements (all run **on your machine**, nothing is sent out):
- Reading text in images: Windows built-in OCR. For correct Vietnamese accents: install Tesseract with the `vie` pack, or copy `vie.traineddata` into `models\tessdata\` (the app rereads all images with the new pack).
- Semantic search and answers: [Ollama](https://ollama.com) with `ollama pull bge-m3` and `ollama pull qwen2.5:7b` (use `qwen2.5:3b` on weaker machines). Without Ollama it still searches by keyword and lists source notes.

### Voice
- **Speak** button in the quick note box: text appears as you talk, edit then Ctrl+Enter to save.
- **Small mic button on the bubble** or **Ctrl+Alt+V**: speak and it saves automatically, understands times ("3 pm tomorrow", "in 30 minutes"), "urgent", "project X", "task … is done", and questions like "what do I have today". Voice commands are in Vietnamese.
- **Record** on the phone: the computer listens and saves automatically.
- The speech model lives in `models\`. On a new machine, the first mic use downloads the `small` model (about 480 MB) from Hugging Face.
- **Assistant** (optional): free-form chat with Ollama `qwen2.5:7b`.

## Security and privacy
- The repo and the download package **contain no personal data** of anyone: `data\` is excluded from git and from the installer.
- The phone version needs its own key, blocks key guessing, blocks unknown websites from calling the app, and uses HTTPS on your home network.
- Details, the list of places the app connects out to, and how to report a vulnerability: [SECURITY.md](SECURITY.md).

## Run from source
```
pip install -r requirements.txt
python ideanote_app.py
```
- `ideanote_app.py`: Qt interface. `server.py`: data, reminders, server for the phone (page `index.html`).
- `knowledge.py`: knowledge base (reading text in images, search, asking AI). `assistant.py` and `voicerules.py`: voice. `mailer.py`: reminders via Gmail and Google Calendar.

## Build a new release
`python build_release.py` creates `release\IdeaNote-v…-win64.zip` (program only, no data), then upload it to the repo's Releases.

## License
MIT, see [LICENSE](LICENSE). Use, modify and share freely, keep the author credit line.


---

Made by [DUCPT](https://ducpt.com/?utm_source=github&utm_medium=readme&utm_campaign=IdeaNote): AI agents, automation and digital products for one-person businesses.
