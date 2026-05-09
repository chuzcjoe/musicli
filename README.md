# musicli

A YouTube music player for your terminal. Search songs and stream audio without leaving the command line.

## Features

- **Search** YouTube with live autocomplete suggestions as you type
- **Stream** audio instantly — nothing is downloaded to disk
- **Progress bar** with real-time position and duration via mpv IPC
- **Playback controls** — pause, seek, volume, mute (powered by mpv)
- **Queue** — add multiple songs and play them in order
- Works entirely in the terminal, no GUI required

## Installation

### Homebrew (recommended)

```bash
brew tap chuzcjoe/musicli
brew install musicli
```

This automatically installs all dependencies (`mpv`, `yt-dlp`, `prompt_toolkit`).

### Manual

**Prerequisites**

```bash
brew install mpv yt-dlp
pip install prompt_toolkit
```

**Run**

```bash
git clone https://github.com/chuzcjoe/musicli.git
cd musicli
python3 musicli.py
```

## Usage

Launch the player:

```bash
musicli
```

| Input | Action |
|---|---|
| `<query>` | Search YouTube (suggestions appear as you type) |
| `1`, `2`, … | Play that result immediately |
| `+1`, `+2`, … | Add that result to the queue |
| `play` | Play the queue from start |
| `queue` | Show current queue |
| `clear` | Clear the queue |
| `help` | Show help |
| `quit` | Exit |

**Playback controls** (while a song is playing via mpv):

| Key | Action |
|---|---|
| `Space` | Pause / resume |
| `←` / `→` | Seek −5s / +5s |
| `9` / `0` | Volume down / up |
| `m` | Mute toggle |
| `q` | Stop and return to musicli |

## How it works

- **Search suggestions** — fetched from the YouTube autocomplete API in a background thread, same data source as the YouTube search bar
- **Playback** — mpv streams audio directly from YouTube via yt-dlp; nothing is written to disk
- **Progress bar** — musicli opens an IPC socket to mpv and subscribes to `time-pos`, `duration`, and `pause` property events, updating the terminal bar in real time

## Dependencies

| Dependency | Purpose | Installed by |
|---|---|---|
| `mpv` | Audio playback | Homebrew |
| `yt-dlp` | YouTube search & stream extraction | Homebrew |
| `prompt_toolkit` | Live search suggestions | Bundled in formula |

## License

MIT
