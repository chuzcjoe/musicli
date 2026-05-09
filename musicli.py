#!/usr/bin/env python3
"""musicli - YouTube music player for the terminal"""

import subprocess
import sys
import shutil
import urllib.request
import urllib.parse
import json
import os
import socket
import threading
import contextlib
from typing import List, Dict, Optional

# ── Optional: prompt_toolkit for live suggestions ─────────────────────────────
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion, ThreadedCompleter
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.styles import Style
    HAS_PTK = True
except ImportError:
    HAS_PTK = False
    # Dummy base so the class definition doesn't fail at import time
    class Completer:  # type: ignore[no-redef]
        pass
    class Completion:  # type: ignore[no-redef]
        pass
    class ThreadedCompleter:  # type: ignore[no-redef]
        pass


# ── ANSI colours ──────────────────────────────────────────────────────────────

class C:
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    BLUE   = '\033[94m'
    BOLD   = '\033[1m'
    DIM    = '\033[2m'
    RESET  = '\033[0m'

def c(color: str, text: str) -> str:
    return f"{color}{text}{C.RESET}"


# ── Dependency helpers ────────────────────────────────────────────────────────

def get_player() -> Optional[str]:
    for p in ('mpv', 'ffplay'):
        if shutil.which(p):
            return p
    return None


def check_deps() -> None:
    if not shutil.which('yt-dlp'):
        print(c(C.RED, '  ✗ yt-dlp') + '  →  pip install yt-dlp')
    if not get_player():
        print(c(C.RED, '  ✗ mpv') + '    →  brew install mpv')
    if not HAS_PTK:
        print(c(C.YELLOW, '  ⚠ prompt_toolkit') +
              '  →  pip install prompt_toolkit  (enables search suggestions)')


# ── YouTube autocomplete suggestions ─────────────────────────────────────────

_suggest_cache: Dict[str, List[str]] = {}

def fetch_suggestions(query: str) -> List[str]:
    """Fetch YouTube search suggestions (same API as the YouTube search bar)."""
    if not query or len(query) < 2:
        return []
    if query in _suggest_cache:
        return _suggest_cache[query]
    try:
        url = (
            'https://suggestqueries.google.com/complete/search'
            f'?client=firefox&ds=yt&q={urllib.parse.quote(query)}'
        )
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read().decode('utf-8'))
        suggestions = data[1] if len(data) > 1 else []
        _suggest_cache[query] = suggestions
        return suggestions
    except Exception:
        return []


_COMMANDS = {'quit', 'exit', 'q', ':q', 'help', 'h', '?', 'queue', 'play', 'clear'}

class YTSuggestCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.strip()

        # Don't suggest for short input, numbers, queue-add syntax, or commands
        if (
            not text
            or len(text) < 2
            or text.isdigit()
            or text.startswith('+')
            or text.lower() in _COMMANDS
        ):
            return

        for s in fetch_suggestions(text):
            if s.strip().lower() != text.lower():
                yield Completion(s, start_position=-len(text))


# ── YouTube search ────────────────────────────────────────────────────────────

def search(query: str, n: int = 10) -> List[Dict]:
    print(f"  {c(C.DIM, 'Searching…')}", end='\r', flush=True)
    try:
        r = subprocess.run(
            [
                'yt-dlp',
                f'ytsearch{n}:{query}',
                '--flat-playlist',
                '--print', '%(title)s\t%(id)s\t%(duration_string)s\t%(channel)s',
                '--quiet',
            ],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        print(f"\n  {c(C.RED, 'yt-dlp not found.')}  Install: pip install yt-dlp")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"\n  {c(C.YELLOW, 'Search timed out. Try again.')}")
        return []

    print(' ' * 40, end='\r')

    songs: List[Dict] = []
    for line in r.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) >= 2 and parts[1]:
            songs.append({
                'title':    parts[0],
                'id':       parts[1],
                'duration': parts[2] if len(parts) > 2 else '—',
                'channel':  parts[3] if len(parts) > 3 else '—',
            })
    return songs


# ── Display ───────────────────────────────────────────────────────────────────

TITLE_W = 52
CHAN_W   = 22

def show_results(songs: List[Dict]) -> None:
    if not songs:
        print(f"  {c(C.YELLOW, 'No results.')}\n")
        return

    print(f"\n  {c(C.BOLD, f'{'#':<4}{'Title':<{TITLE_W}}{'Time':<9}Channel')}")
    print(f"  {c(C.DIM, '─' * 82)}")

    for i, s in enumerate(songs, 1):
        title = s['title']
        if len(title) > TITLE_W - 1:
            title = title[:TITLE_W - 2] + '…'
        chan = s['channel']
        if len(chan) > CHAN_W - 1:
            chan = chan[:CHAN_W - 2] + '…'
        dur = s['duration']
        print(
            f"  {c(C.CYAN, f'{i:<4}')}"
            f"{title:<{TITLE_W}}"
            f"{c(C.DIM, f'{dur:<9}')}"
            f"{c(C.YELLOW, chan)}"
        )
    print()


def show_queue(queue: List[Dict]) -> None:
    if not queue:
        print(f"  {c(C.DIM, 'Queue is empty.')}\n")
        return
    print(f"\n  {c(C.BOLD, 'Queue:')}")
    for i, s in enumerate(queue, 1):
        print(f"  {c(C.CYAN, f'{i}.')} {s['title']}")
    print()


# ── Now-playing UI ───────────────────────────────────────────────────────────

def print_now_playing(song: Dict, player: str) -> None:
    W = 56  # visible chars between the two │ borders

    def row(plain: str, colored: str = '') -> None:
        """Print one box row; plain is used for width, colored for display."""
        if not colored:
            colored = plain
        pad = max(0, W - 1 - len(plain))
        print(f"  {c(C.CYAN, '│')} {colored}{' ' * pad}{c(C.CYAN, '│')}")

    def sep(l: str = '├', r: str = '┤') -> None:
        print(f"  {c(C.CYAN, l + '─' * W + r)}")

    title = song['title']
    if len(title) > W - 1:
        title = title[:W - 2] + '…'

    chan = song.get('channel', '—')
    if len(chan) > 30:
        chan = chan[:29] + '…'
    dur  = song.get('duration', '—')
    meta = f"{chan}   {dur}"

    if player == 'mpv':
        controls = [
            ('SPACE',  'pause / resume'),
            ('← / →',  'seek  −5s / +5s'),
            ('9 / 0',  'volume down / up'),
            ('m',      'mute toggle'),
            ('q',      'stop & return to musicli'),
        ]
    else:
        controls = [
            ('Ctrl+C', 'stop & return to musicli'),
        ]

    print()
    sep('┌', '┐')
    row('▶  Now Playing',
        f"{c(C.GREEN + C.BOLD, '▶')}{c(C.BOLD, '  Now Playing')}")
    sep()
    row(title,  c(C.BOLD, title))
    row(meta,   c(C.DIM, meta))
    sep()
    for key, desc in controls:
        plain   = f"  {key:<9}{desc}"
        colored = f"  {c(C.CYAN + C.BOLD, f'{key:<9}')}{c(C.DIM, desc)}"
        row(plain, colored)
    sep('└', '┘')
    print()


# ── Progress bar ─────────────────────────────────────────────────────────────

_BAR_W = 36   # width of the ░/█ fill region

def _fmt_time(secs: float) -> str:
    s = int(secs)
    m, s = divmod(s, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def _render_bar(pos: float, dur: float, paused: bool) -> str:
    """Return a \r-prefixed progress line ready to overwrite the current terminal row."""
    pct    = (pos / dur) if dur > 0 else 0.0
    filled = int(_BAR_W * pct)
    bar    = '█' * filled + '░' * (_BAR_W - filled)
    pos_s  = _fmt_time(pos)
    dur_s  = _fmt_time(dur) if dur > 0 else '?:??'
    sym    = '⏸' if paused else '▶'
    col    = C.YELLOW if paused else C.GREEN
    return (
        f"\r  {c(col + C.BOLD, sym)}"
        f"  [{c(col, bar)}]"
        f"  {c(C.BOLD, pos_s)} / {c(C.DIM, dur_s)}"
        f"        "   # trailing spaces clear any leftover chars
    )


def _play_mpv_ipc(url: str) -> None:
    """Run mpv with an IPC socket; stream live time-pos/duration/pause to a progress bar."""
    sock_path  = f"/tmp/musicli-{os.getpid()}.sock"
    stop_evt   = threading.Event()

    # Print the initial progress line without a newline so \r can overwrite it.
    print(f"  {c(C.GREEN + C.BOLD, '▶')}  [{'░' * _BAR_W}]  0:00 / ?:??", end='', flush=True)

    def ipc_loop() -> None:
        # Wait up to 5 s for mpv to create the socket file.
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            import time; time.sleep(0.1)
        else:
            return

        time_pos = 0.0
        duration = 0.0
        paused   = False
        buf      = ''
        sock     = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(sock_path)
            sock.settimeout(1.0)
            # Ask mpv to push updates whenever these properties change.
            for obs_id, prop in [(1, 'time-pos'), (2, 'duration'), (3, 'pause')]:
                cmd = json.dumps({'command': ['observe_property', obs_id, prop]}) + '\n'
                sock.sendall(cmd.encode())

            while not stop_evt.is_set():
                try:
                    chunk = sock.recv(4096).decode('utf-8', errors='ignore')
                    if not chunk:
                        break
                    buf += chunk
                    while '\n' in buf:
                        line, buf = buf.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg  = json.loads(line)
                            name = msg.get('name')
                            val  = msg.get('data')
                            if msg.get('event') != 'property-change' or val is None:
                                continue
                            if name == 'time-pos':
                                time_pos = float(val)
                                print(_render_bar(time_pos, duration, paused), end='', flush=True)
                            elif name == 'duration':
                                duration = float(val)
                                print(_render_bar(time_pos, duration, paused), end='', flush=True)
                            elif name == 'pause':
                                paused = bool(val)
                                print(_render_bar(time_pos, duration, paused), end='', flush=True)
                        except (json.JSONDecodeError, ValueError):
                            pass
                except socket.timeout:
                    continue
                except OSError:
                    break
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                if sock:
                    sock.close()

    t = threading.Thread(target=ipc_loop, daemon=True)
    t.start()

    try:
        subprocess.run([
            'mpv', '--no-video', '--really-quiet',
            f'--input-ipc-server={sock_path}',
            url,
        ])
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        t.join(timeout=2.0)
        with contextlib.suppress(OSError):
            os.unlink(sock_path)
        print()   # end the progress line
        print()   # blank line


# ── Playback ──────────────────────────────────────────────────────────────────

def play(song: Dict) -> None:
    url = f"https://www.youtube.com/watch?v={song['id']}"
    player = get_player()

    if not player:
        print(f"  {c(C.RED, 'No player found.')}  Install: brew install mpv\n")
        return

    print_now_playing(song, player)

    if player == 'mpv':
        _play_mpv_ipc(url)
        return

    elif player == 'ffplay':
        print(f"  {c(C.DIM, 'Ctrl+C to stop')}\n")
        try:
            r = subprocess.run(
                ['yt-dlp', '-x', '--get-url', '--quiet', url],
                capture_output=True, text=True, timeout=20,
            )
            audio_url = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ''
            if audio_url:
                subprocess.run(
                    ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'error', audio_url]
                )
            else:
                print(f"  {c(C.RED, 'Could not get audio URL.')}\n")
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"  {c(C.RED, f'Playback error: {e}')}\n")

    print(f"  {c(C.DIM, 'Done.')}\n")


def play_queue(queue: List[Dict]) -> List[Dict]:
    while queue:
        play(queue.pop(0))
    return queue


# ── Help & banner ─────────────────────────────────────────────────────────────

BANNER = f"""
{c(C.CYAN + C.BOLD, '  musicli')}  {c(C.DIM, '— YouTube music in your terminal')}
  {c(C.DIM, '─' * 42)}"""

HELP = f"""
  {c(C.BOLD, 'Search & play')}
    {c(C.CYAN, '<query>')}          search YouTube  {c(C.DIM, '(suggestions appear as you type)')}
    {c(C.CYAN, '<number>')}         play a result immediately
    {c(C.CYAN, '+<number>')}        add result to queue
    {c(C.CYAN, 'play')}             play queue from start
    {c(C.CYAN, 'queue')}            show current queue
    {c(C.CYAN, 'clear')}            clear queue

  {c(C.BOLD, 'Other')}
    {c(C.CYAN, 'help')}             show this message
    {c(C.CYAN, 'quit')}             exit
"""


# ── Input session ─────────────────────────────────────────────────────────────

def make_session():
    if not HAS_PTK:
        return None

    style = Style.from_dict({
        'completion-menu.completion':         'bg:#1e2030 fg:#89b4fa',
        'completion-menu.completion.current': 'bg:#313244 fg:#cdd6f4 bold',
        'scrollbar.background':               'bg:#1e2030',
        'scrollbar.button':                   'bg:#45475a',
    })

    return PromptSession(
        completer=ThreadedCompleter(YTSuggestCompleter()),
        complete_while_typing=True,
        style=style,
    )


def get_input(session, prompt_str: str) -> str:
    if session:
        return session.prompt(ANSI(prompt_str))
    return input(prompt_str)


# ── Main REPL ─────────────────────────────────────────────────────────────────

def main() -> None:
    print(BANNER)

    if not shutil.which('yt-dlp') or not get_player() or not HAS_PTK:
        print(f"\n  {c(C.YELLOW, 'Dependencies:')}")
        check_deps()

    print(HELP)

    session   = make_session()
    prompt_str = f"{c(C.BOLD + C.BLUE, '  ♪')}  "

    results: List[Dict] = []
    queue:   List[Dict] = []

    while True:
        try:
            raw = get_input(session, prompt_str).strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n  {c(C.DIM, 'Bye!')}\n")
            break

        if not raw:
            continue

        low = raw.lower()

        if low in ('quit', 'exit', 'q', ':q'):
            print(f"\n  {c(C.DIM, 'Bye!')}\n")
            break

        if low in ('help', 'h', '?'):
            print(HELP)
            continue

        if low == 'queue':
            show_queue(queue)
            continue

        if low == 'clear':
            queue.clear()
            print(f"  {c(C.DIM, 'Queue cleared.')}\n")
            continue

        if low == 'play':
            if not queue:
                print(f"  {c(C.YELLOW, 'Queue is empty. Add songs with +<number>.')}\n")
            else:
                queue = play_queue(queue)
            continue

        if raw.startswith('+') and raw[1:].isdigit():
            idx = int(raw[1:]) - 1
            if not results:
                print(f"  {c(C.YELLOW, 'Search for something first.')}\n")
            elif 0 <= idx < len(results):
                queue.append(results[idx])
                print(f"  {c(C.GREEN, '+')} Added: {results[idx]['title']}\n")
            else:
                print(f"  {c(C.YELLOW, f'Enter a number between 1 and {len(results)}.')}\n")
            continue

        if raw.isdigit():
            idx = int(raw) - 1
            if not results:
                print(f"  {c(C.YELLOW, 'Search for something first.')}\n")
            elif 0 <= idx < len(results):
                play(results[idx])
            else:
                print(f"  {c(C.YELLOW, f'Enter a number between 1 and {len(results)}.')}\n")
            continue

        query = raw.removeprefix('search ').strip()
        if query:
            results = search(query)
            show_results(results)


if __name__ == '__main__':
    main()
