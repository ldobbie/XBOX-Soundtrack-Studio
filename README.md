# Xbox Soundtrack Studio

A cross-platform desktop tool for managing **custom soundtracks** on original
Xbox hard-drive images — the kind used by the [xemu](https://xemu.app) emulator
and [Batocera](https://batocera.org). It bundles every step of the process into
one graphical app: open a disk image, convert your music, inject it into the
Xbox's soundtrack database, and save — with **no need to install qemu** and no
manual filesystem wrangling.

Custom soundtracks are the original Xbox feature that lets supported games play
your own music in place of their built-in tracks. Setting them up by hand
normally means juggling several different tools; this app does it all in one
place.

---

## Features

- **Open `.qcow2` or `.img` Xbox hard-drive images.** Compressed qcow2 images
  are decompressed automatically using a built-in, pure-Python implementation —
  qemu does not need to be installed.
- **Browse existing soundtracks and tracks** in an expandable tree view, with
  track names and durations.
- **Add soundtracks** from a folder of audio. Files are converted to the Xbox's
  WMA format automatically (via ffmpeg).
- **Granular track editing:** add individual tracks to a soundtrack, remove
  single tracks, or move a track from one soundtrack to another.
- **Soundtrack management:** create empty soundtracks, rename, and remove.
- **Safe saving:** *Save* overwrites the original image using an atomic
  write (temp file then swap), so an interrupted save can't corrupt your image.
  *Save As…* defaults to the image's own folder.
- **Format-correct output**, validated against `qemu-img` and real-hardware
  soundtrack layouts.

---

## Requirements

- **Python 3.9 or newer**
  - Windows / macOS: install from [python.org](https://www.python.org/downloads/)
    (these builds include Tkinter, the GUI toolkit, by default).
  - Linux: install Python 3 and Tkinter via your package manager, e.g.
    `sudo apt install python3 python3-tk`.
- **ffmpeg** (provides `ffmpeg` and `ffprobe`), used for audio conversion.
  - Windows: `winget install Gyan.FFmpeg`, or download from
    [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add its `bin` folder to PATH.
  - macOS: `brew install ffmpeg` (see [brew.sh](https://brew.sh)), or a static
    build from [evermeet.cx](https://evermeet.cx/ffmpeg/).
  - Linux: `sudo apt install ffmpeg` or your distro's equivalent.

The qcow2 and FATX filesystem handling are pure Python with no third-party
packages required; `ffmpeg`/`ffprobe` need to be available on your system for
audio conversion.

---

## Running

From the project folder:

```bash
python3 app.py
```

On Windows you can also use `python app.py` or `py app.py`, depending on how
Python was installed.

---

## Usage

1. **Open** an Xbox hard-drive image (`.qcow2` or `.img`).
2. Existing soundtracks appear in the list; expand one to see its tracks.
3. To add a soundtrack: choose a folder of audio files, give it a name, and
   click **Convert + Inject**.
4. Use the editing buttons to add/remove/move individual tracks, or to rename
   and remove whole soundtracks.
5. Click **Save** to write changes back to the image (or **Save As…** for a
   copy).
6. Copy the saved image back to your device.

> **Always keep a backup of your original image before overwriting it.**

```

Consult your emulator or device documentation for the exact location of the
Xbox hard-drive image.

---

## How it works

The app is organised into a small engine plus a GUI:

```
.
├── app.py                  # GUI application
└── engine/
    ├── qcow2.py            # pure-Python qcow2 <-> raw image conversion
    ├── fatx.py             # FATX filesystem reader/writer
    └── soundtrack.py       # audio conversion + ST.DB database builder
```

- **qcow2** images are expanded to raw and recompressed entirely in Python, so
  qemu is not a dependency. Output is verified to pass `qemu-img check`.
- **FATX** is the Xbox's proprietary filesystem. The engine reads and writes it
  directly inside the image. Write operations are confined to the soundtrack
  area of the data partition.
- **Soundtracks** are stored in a binary database (`ST.DB`) alongside WMA audio
  files in a specific folder/index scheme. The builder reproduces this layout
  exactly so supported games and the dashboard recognise the music.

---

## Notes and limitations

- Audio is converted to WMA. The codec defaults to a widely compatible setting;
  an alternate WMA codec is available if a particular title is picky.
- Track titles are derived from filenames (a leading track number such as
  `01. ` is stripped) and are subject to the Xbox's length limits.
- The filesystem writer only modifies the soundtrack area of the image. Even so,
  working on a copy is strongly recommended.
- Removing a soundtrack can leave a gap in internal folder numbering; this is
  harmless and does not affect playback.

---

## Compatibility

- Works with Xbox hard-drive images using the standard retail partition layout.
- Tested with images produced by common emulator/device workflows.
- Custom soundtracks are read by supported games directly from the disk image,
  so a dashboard is not required for playback. Whether a given title plays them
  depends on that game's own support and, when emulating, on the emulator's
  handling of the soundtrack system.

---

## Disclaimer

This tool operates only on disk images that **you supply**. It does not include,
distribute, or download any copyrighted material — no system software, dashboard
files, BIOS images, or audio. You are responsible for ensuring you have the
right to modify the images and to use any audio you add. Always keep backups.

---

## License

Released under the MIT License. See [LICENSE](LICENSE) for details.

---

## Contributing

Issues and pull requests are welcome. Bug reports are most useful when they
include the platform, the type of image being edited, and the steps to
reproduce the problem.
