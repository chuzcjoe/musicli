"""Session-based song recommender for musicli.

Keeps a record of every song played during the current session and suggests
the next track based on the most recently played one.  This module is
intentionally UI-free so it can be reused or tested independently of the
musicli REPL.
"""

import subprocess
from typing import Dict, List, Optional


class SessionRecommender:
    """In-memory history + simple next-track suggestion via yt-dlp search."""

    def __init__(self) -> None:
        self._history:    List[Dict] = []
        self._played_ids: set        = set()

    # ── History tracking ──────────────────────────────────────────────────

    def record(self, song: Dict) -> None:
        """Mark *song* as played for the current session."""
        sid = song.get('id')
        if not sid:
            return
        self._history.append(song)
        self._played_ids.add(sid)

    def has_history(self) -> bool:
        return bool(self._history)

    # ── Recommendation ────────────────────────────────────────────────────

    def recommend(self) -> Optional[Dict]:
        """Return the next recommended song, or ``None`` if unavailable.

        The most recently played song is used as the seed.  We search YouTube
        with a query derived from its channel + title head and pick the first
        result that hasn't already been heard in this session.
        """
        if not self._history:
            return None
        seed  = self._history[-1]
        query = self._build_query(seed)
        if not query:
            return None
        for cand in self._search(query, limit=15):
            if cand['id'] not in self._played_ids:
                return cand
        return None

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_query(song: Dict) -> str:
        """Derive a YouTube-search query from a played song."""
        title   = (song.get('title')   or '').strip()
        channel = (song.get('channel') or '').strip()
        # The part before " - " is usually the artist or track name.
        head    = title.split(' - ', 1)[0].strip() if title else ''
        parts   = [p for p in (channel, head) if p and p != '—']
        return ' '.join(parts)[:120]

    @staticmethod
    def _search(query: str, limit: int = 10) -> List[Dict]:
        """Flat yt-dlp search returning a list of song dicts."""
        try:
            r = subprocess.run(
                [
                    'yt-dlp',
                    f'ytsearch{limit}:{query}',
                    '--flat-playlist',
                    '--print', '%(title)s\t%(id)s\t%(duration_string)s\t%(channel)s',
                    '--quiet',
                ],
                capture_output=True, text=True, timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

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
