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
import http.server
import hashlib
import base64
import secrets
import time
import webbrowser
import select
import tty
import termios
from typing import List, Dict, Optional

from recommender import SessionRecommender

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


_COMMANDS = {
    'quit', 'exit', 'q', ':q', 'help', 'h', '?',
    'queue', 'play', 'clear',
    'login', 'logout',
}

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
    sock_path = f"/tmp/musicli-{os.getpid()}.sock"
    stop_evt  = threading.Event()

    print(f"  {c(C.GREEN + C.BOLD, '▶')}  [{'░' * _BAR_W}]  0:00 / ?:??", end='', flush=True)

    def ipc_loop() -> None:
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.1)
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
        print()
        print()


# ── Playback ──────────────────────────────────────────────────────────────────

def play(song: Dict) -> None:
    url    = f"https://www.youtube.com/watch?v={song['id']}"
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


def play_queue(queue: List[Dict], on_play=None) -> List[Dict]:
    while queue:
        song = queue.pop(0)
        play(song)
        if on_play:
            on_play(song)
    return queue


def _wait_for_cancel(seconds: float) -> bool:
    """Sleep up to *seconds*; return True if the user pressed 'q' (or Ctrl+C)."""
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except Exception:
        # No real TTY available — fall back to a plain sleep.
        try:
            time.sleep(seconds)
        except KeyboardInterrupt:
            return True
        return False

    try:
        tty.setcbreak(fd)
        deadline = time.time() + seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            try:
                r, _, _ = select.select([fd], [], [], remaining)
            except KeyboardInterrupt:
                return True
            if not r:
                return False
            try:
                ch = os.read(fd, 1)
            except OSError:
                return False
            if ch in (b'q', b'Q', b'\x03'):   # q / Q / Ctrl+C
                return True
            # any other key is ignored; keep waiting
    finally:
        with contextlib.suppress(Exception):
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def autoplay_loop(queue: List[Dict], recommender: SessionRecommender) -> None:
    """Keep playing recommended songs while the queue stays empty.

    Stops when the recommender returns nothing or the user presses 'q' during
    the "up next" countdown.
    """
    while not queue:
        rec = recommender.recommend()
        if not rec:
            return
        print(
            f"  {c(C.CYAN + C.BOLD, '♪')} Up next: "
            f"{c(C.BOLD, rec['title'])}  "
            f"{c(C.DIM, '(press q to cancel)')}"
        )
        if _wait_for_cancel(2.5):
            print(f"  {c(C.DIM, 'Auto-play stopped.')}\n")
            return
        play(rec)
        recommender.record(rec)


# ── Auth (Google OAuth 2.0 PKCE) ─────────────────────────────────────────────
#
# Create credentials at: console.cloud.google.com → APIs & Services → Credentials
# Application type: Desktop app.  Then set the two env vars below.

_GOOGLE_CLIENT_ID     = os.environ.get('MUSICLI_GOOGLE_CLIENT_ID', '')
_GOOGLE_CLIENT_SECRET = os.environ.get('MUSICLI_GOOGLE_CLIENT_SECRET', '')
_GOOGLE_SCOPES        = 'openid email profile'
_AUTH_FILE            = os.path.expanduser('~/.config/musicli/auth.json')


def _load_auth() -> Optional[Dict]:
    try:
        with open(_AUTH_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _save_auth(data: Dict) -> None:
    os.makedirs(os.path.dirname(_AUTH_FILE), exist_ok=True)
    with open(_AUTH_FILE, 'w') as f:
        json.dump(data, f)


def _pkce_pair() -> tuple:
    verifier  = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b'=').decode()
    return verifier, challenge


def _exchange_code(code: str, verifier: str, redirect_uri: str) -> Optional[Dict]:
    body = urllib.parse.urlencode({
        'client_id':     _GOOGLE_CLIENT_ID,
        'client_secret': _GOOGLE_CLIENT_SECRET,
        'code':          code,
        'code_verifier': verifier,
        'grant_type':    'authorization_code',
        'redirect_uri':  redirect_uri,
    }).encode()
    req = urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=body, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _fetch_user_info(access_token: str) -> Optional[Dict]:
    req = urllib.request.Request(
        'https://www.googleapis.com/oauth2/v3/userinfo',
        headers={'Authorization': f'Bearer {access_token}'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _do_token_refresh(refresh_tok: str) -> Optional[Dict]:
    body = urllib.parse.urlencode({
        'client_id':     _GOOGLE_CLIENT_ID,
        'client_secret': _GOOGLE_CLIENT_SECRET,
        'refresh_token': refresh_tok,
        'grant_type':    'refresh_token',
    }).encode()
    req = urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=body, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def get_current_user() -> Optional[Dict]:
    """Return cached user info, transparently refreshing the access token if needed."""
    auth = _load_auth()
    if not auth:
        return None
    if time.time() < auth.get('expires_at', 0) - 60:
        return auth.get('user_info')
    refresh = auth.get('refresh_token')
    if refresh and _GOOGLE_CLIENT_ID and _GOOGLE_CLIENT_SECRET:
        new_tok = _do_token_refresh(refresh)
        if new_tok and 'access_token' in new_tok:
            auth.update(new_tok)
            auth['expires_at'] = time.time() + new_tok.get('expires_in', 3600)
            if not new_tok.get('refresh_token'):
                auth['refresh_token'] = refresh
            _save_auth(auth)
            return auth.get('user_info')
    with contextlib.suppress(OSError):
        os.unlink(_AUTH_FILE)
    return None


def login_with_google() -> Optional[Dict]:
    """Run the Google OAuth PKCE flow in the browser; return user_info on success."""
    if not _GOOGLE_CLIENT_ID or not _GOOGLE_CLIENT_SECRET:
        print(f"\n  {c(C.RED, 'Google OAuth not configured.')}")
        print(f"  {c(C.DIM, 'Set MUSICLI_GOOGLE_CLIENT_ID and MUSICLI_GOOGLE_CLIENT_SECRET.')}\n")
        return None

    with socket.socket() as _s:
        _s.bind(('localhost', 0))
        port = _s.getsockname()[1]

    redirect_uri        = f'http://localhost:{port}/callback'
    verifier, challenge = _pkce_pair()
    state               = secrets.token_urlsafe(16)
    auth_code: list     = [None]
    stop_event          = threading.Event()

    auth_url = (
        'https://accounts.google.com/o/oauth2/v2/auth?'
        + urllib.parse.urlencode({
            'client_id':             _GOOGLE_CLIENT_ID,
            'redirect_uri':          redirect_uri,
            'response_type':         'code',
            'scope':                 _GOOGLE_SCOPES,
            'state':                 state,
            'code_challenge':        challenge,
            'code_challenge_method': 'S256',
            'access_type':           'offline',
            'prompt':                'select_account',
        })
    )

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed    = urllib.parse.urlparse(self.path)
            params    = urllib.parse.parse_qs(parsed.query)
            code      = params.get('code', [None])[0]
            got_state = params.get('state', [None])[0]

            if parsed.path == '/callback' and code and got_state == state:
                auth_code[0] = code
                body = (
                    b'<html><body style="font-family:system-ui;text-align:center;'
                    b'padding:80px;background:#f6fef9">'
                    b'<h2 style="color:#2d6a4f">&#10003; Login successful!</h2>'
                    b'<p style="color:#555">You can close this tab and return to the terminal.</p>'
                    b'</body></html>'
                )
                self.send_response(200)
                stop_event.set()
            else:
                body = b''
                self.send_response(204)

            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server     = http.server.HTTPServer(('localhost', port), _Handler)
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    print(f"\n  {c(C.DIM, 'Opening browser for Google login…')}")
    webbrowser.open(auth_url)
    print(f"  {c(C.DIM, 'Waiting for login… (Ctrl+C to cancel)')}\n")

    try:
        stop_event.wait(timeout=120)
    except KeyboardInterrupt:
        print(f"\n  {c(C.YELLOW, 'Login cancelled.')}\n")
        server.shutdown()
        server.server_close()
        return None

    server.shutdown()
    server.server_close()

    if not auth_code[0]:
        print(f"  {c(C.YELLOW, 'Login timed out or was cancelled.')}\n")
        return None

    tokens = _exchange_code(auth_code[0], verifier, redirect_uri)
    if not tokens or 'access_token' not in tokens:
        print(f"  {c(C.RED, 'Failed to exchange auth code for tokens.')}\n")
        return None

    user_info = _fetch_user_info(tokens['access_token'])
    if not user_info:
        print(f"  {c(C.RED, 'Failed to get user info.')}\n")
        return None

    _save_auth({
        **tokens,
        'user_info':  user_info,
        'expires_at': time.time() + tokens.get('expires_in', 3600),
    })
    return user_info


def do_logout() -> None:
    with contextlib.suppress(OSError):
        os.unlink(_AUTH_FILE)
    print(f"  {c(C.GREEN, '✓')} Logged out.\n")


def show_welcome() -> Optional[Dict]:
    """Show login / guest choice; return user_info or None (guest)."""
    existing = get_current_user()
    if existing:
        name = existing.get('name') or existing.get('email', 'there')
        print(f"\n  {c(C.GREEN, '✓')} Welcome back, {c(C.BOLD, name)}!\n")
        return existing

    print(f"\n  {c(C.BOLD, 'How would you like to continue?')}\n")
    print(f"  {c(C.CYAN, '1.')} Continue as guest")
    print(f"  {c(C.CYAN, '2.')} Log in with Google\n")

    try:
        choice = input(f"  {c(C.DIM, 'Your choice [1/2]:')} ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return None

    if choice == '2':
        user = login_with_google()
        if user:
            name = user.get('name') or user.get('email', 'there')
            print(f"  {c(C.GREEN, '✓')} Logged in as {c(C.BOLD, name)}!\n")
        return user

    print()
    return None



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

  {c(C.BOLD, 'Account')}
    {c(C.CYAN, 'login')}            log in with Google
    {c(C.CYAN, 'logout')}           log out of your account

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

VERSION = "1.0.0"

def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ('--version', '-v'):
        print(f"musicli {VERSION}")
        sys.exit(0)

    print(BANNER)

    if not shutil.which('yt-dlp') or not get_player() or not HAS_PTK:
        print(f"\n  {c(C.YELLOW, 'Dependencies:')}")
        check_deps()

    current_user = show_welcome()

    print(f"  {c(C.DIM, 'Search for a song to get started. Type')} "
          f"{c(C.CYAN, 'help')} {c(C.DIM, 'to see all commands.')}\n")

    session   = make_session()
    prompt_str = f"{c(C.BOLD + C.BLUE, '  ♪')}  "

    results:     List[Dict]         = []
    queue:       List[Dict]         = []
    recommender: SessionRecommender = SessionRecommender()

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

        if low == 'login':
            current_user = login_with_google()
            if current_user:
                name = current_user.get('name') or current_user.get('email', 'there')
                print(f"  {c(C.GREEN, '✓')} Logged in as {c(C.BOLD, name)}!\n")
            continue

        if low == 'logout':
            do_logout()
            current_user = None
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
                queue = play_queue(queue, on_play=recommender.record)
                autoplay_loop(queue, recommender)
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
                recommender.record(results[idx])
                autoplay_loop(queue, recommender)
            else:
                print(f"  {c(C.YELLOW, f'Enter a number between 1 and {len(results)}.')}\n")
            continue

        query = raw.removeprefix('search ').strip()
        if query:
            results = search(query)
            show_results(results)


if __name__ == '__main__':
    main()
