#!/usr/bin/env python3
"""
Spotify AI Playlist Sorter - Backend Server
Run this with: python server.py
"""

import os
import json
import math
import urllib.parse
import urllib.request
import http.server
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── CONFIG (user fills these in) ────────────────────────────────────────────
SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID", "YOUR_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_KEY")
PORT                  = int(os.environ.get("PORT", 8888))
RENDER_URL            = os.environ.get("RENDER_EXTERNAL_URL", "")
BASE_URL              = RENDER_URL.rstrip("/") if RENDER_URL else f"http://127.0.0.1:{PORT}"
REDIRECT_URI          = f"{BASE_URL}/callback"
# ─────────────────────────────────────────────────────────────────────────────

SCOPES = "user-library-read user-library-modify playlist-modify-public playlist-modify-private"

# Debug
print(f"🔗 REDIRECT_URI = {REDIRECT_URI}")

# Simple in-memory token store
token_store = {}


def spotify_request(path, token, method="GET", body=None):
    url = f"https://api.spotify.com/v1{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    if body:
        req.data = json.dumps(body).encode()
        req.method = method
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get_liked_songs(token):
    """Fetch all liked songs with pagination."""
    songs = []
    url = "/me/tracks?limit=50&offset=0"
    while url:
        data = spotify_request(url, token)
        for item in data.get("items", []):
            t = item["track"]
            if t:
                songs.append({
                    "id": t["id"],
                    "name": t["name"],
                    "artist": t["artists"][0]["name"] if t["artists"] else "Unknown",
                    "album": t["album"]["name"],
                    "image": t["album"]["images"][0]["url"] if t["album"]["images"] else None,
                    "added_at": item["added_at"],
                })
        next_url = data.get("next")
        if next_url:
            url = next_url.replace("https://api.spotify.com/v1", "")
        else:
            url = None
    return songs


def get_audio_features(token, track_ids):
    """Fetch audio features in batches of 100."""
    features = {}
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i+100]
        ids_str = ",".join(batch)
        data = spotify_request(f"/audio-features?ids={ids_str}", token)
        for f in (data.get("audio_features") or []):
            if f:
                features[f["id"]] = {
                    "energy":       round(f["energy"], 2),
                    "valence":      round(f["valence"], 2),
                    "tempo":        round(f["tempo"]),
                    "danceability": round(f["danceability"], 2),
                    "acousticness": round(f["acousticness"], 2),
                    "instrumentalness": round(f["instrumentalness"], 2),
                }
    return features


def ask_claude(songs):
    """Send songs to Claude and get playlist suggestions."""
    # Trim to 300 songs max to keep prompt size reasonable
    sample = songs[:300]

    songs_text = "\n".join(
        f'- "{s["name"]}" by {s["artist"]} (added: {s.get("added_at","")[:7]})'
        for s in sample
    )

    prompt = f"""You are a music curator AI. A user has {len(songs)} liked songs on Spotify that are cluttering their library. Your job is to group them into 5-12 meaningful playlists they would actually listen to — like a real human music fan would curate them.

Here are their songs (up to 300 shown):
{songs_text}

Rules:
- Create 5 to 12 playlists maximum
- Each playlist should have a compelling, evocative name (not generic like "Playlist 1")
- Each playlist needs a short description (1-2 sentences) explaining the vibe
- Assign each song ID to exactly ONE playlist
- Think about actual listening contexts: working out, late night, focus/study, road trip, cooking, etc.
- Songs not fitting any clear group can go in a "Miscellaneous" or "Discoveries" playlist

Respond ONLY with valid JSON in this exact format:
{{
  "playlists": [
    {{
      "name": "Playlist Name",
      "description": "Short vibe description",
      "song_ids": ["track_id_1", "track_id_2", ...]
    }}
  ]
}}"""

    req_body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request("https://api.anthropic.com/v1/messages")
    req.add_header("x-api-key", ANTHROPIC_API_KEY)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("content-type", "application/json")
    req.data = req_body

    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read())

    text = result["content"][0]["text"]
    # Extract JSON from response
    start = text.find("{")
    end   = text.rfind("}") + 1
    return json.loads(text[start:end])


def unlike_songs(token, track_ids):
    """Remove tracks from Liked Songs in batches of 50 (Spotify limit)."""
    for i in range(0, len(track_ids), 50):
        batch = track_ids[i:i+50]
        ids_str = ",".join(batch)
        req = urllib.request.Request(
            f"https://api.spotify.com/v1/me/tracks?ids={ids_str}"
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        req.method = "DELETE"
        with urllib.request.urlopen(req) as r:
            pass  # 200 OK, no body


def create_playlist(token, user_id, name, description, track_ids):
    """Create a Spotify playlist and add tracks."""
    playlist = spotify_request(
        f"/users/{user_id}/playlists",
        token,
        method="POST",
        body={"name": name, "description": description, "public": False}
    )
    playlist_id = playlist["id"]
    # Add tracks in batches of 100
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i+100]
        uris = [f"spotify:track:{tid}" for tid in batch]
        spotify_request(
            f"/playlists/{playlist_id}/tracks",
            token,
            method="POST",
            body={"uris": uris}
        )
    return playlist_id


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence default logging

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type="text/html"):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.send_file(os.path.join(os.path.dirname(__file__), "index.html"))

        elif path == "/login":
            auth_url = (
                "https://accounts.spotify.com/authorize?"
                + urllib.parse.urlencode({
                    "client_id":     SPOTIFY_CLIENT_ID,
                    "response_type": "code",
                    "redirect_uri":  REDIRECT_URI,
                    "scope":         SCOPES,
                })
            )
            self.send_response(302)
            self.send_header("Location", auth_url)
            self.end_headers()

        elif path == "/callback":
            code = params.get("code", [None])[0]
            if not code:
                self.send_json({"error": "No code returned"}, 400)
                return
            # Exchange code for token
            body = urllib.parse.urlencode({
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  REDIRECT_URI,
                "client_id":     SPOTIFY_CLIENT_ID,
                "client_secret": SPOTIFY_CLIENT_SECRET,
            }).encode()
            req = urllib.request.Request(
                "https://accounts.spotify.com/api/token",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            with urllib.request.urlopen(req) as r:
                token_data = json.loads(r.read())
            access_token = token_data["access_token"]
            token_store["token"] = access_token
            # Redirect back to app
            self.send_response(302)
            self.send_header("Location", f"/?logged_in=1&token={access_token}")
            self.end_headers()

        elif path == "/api/analyze":
            token = params.get("token", [None])[0] or token_store.get("token")
            if not token:
                self.send_json({"error": "Not authenticated"}, 401)
                return
            try:
                print("📦 Fetching liked songs...")
                songs = get_liked_songs(token)
                print(f"   Got {len(songs)} songs")

                print("🤖 Asking Claude to suggest playlists...")
                suggestions = ask_claude(songs)

                # Build id → song map for response
                song_map = {s["id"]: s for s in songs}

                # Enrich suggestions with song details
                enriched = []
                for pl in suggestions.get("playlists", []):
                    enriched_songs = [
                        song_map[sid] for sid in pl["song_ids"] if sid in song_map
                    ]
                    enriched.append({
                        "name": pl["name"],
                        "description": pl["description"],
                        "songs": enriched_songs,
                    })

                self.send_json({"playlists": enriched, "total": len(songs)})
            except Exception as e:
                print(f"❌ Error: {e}")
                self.send_json({"error": str(e)}, 500)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        if path == "/api/create":
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            token  = body.get("token") or token_store.get("token")

            if not token:
                self.send_json({"error": "Not authenticated"}, 401)
                return
            try:
                user = spotify_request("/me", token)
                user_id = user["id"]
                created = []
                all_sorted_ids = []

                for pl in body.get("playlists", []):
                    track_ids = [s["id"] for s in pl["songs"]]
                    pid = create_playlist(token, user_id, pl["name"], pl["description"], track_ids)
                    created.append({"name": pl["name"], "id": pid, "count": len(track_ids)})
                    all_sorted_ids.extend(track_ids)
                    print(f"✅ Created: {pl['name']} ({len(track_ids)} songs)")

                # Remove sorted songs from Liked Songs if requested
                if body.get("unlike", False) and all_sorted_ids:
                    # Deduplicate (a song could appear in multiple playlists)
                    unique_ids = list(dict.fromkeys(all_sorted_ids))
                    print(f"🗑️  Removing {len(unique_ids)} songs from Liked Songs...")
                    unlike_songs(token, unique_ids)
                    print("   Done.")

                self.send_json({"created": created, "unliked": len(all_sorted_ids) if body.get("unlike") else 0})
            except Exception as e:
                print(f"❌ Error creating playlists: {e}")
                self.send_json({"error": str(e)}, 500)


def main():
    print("\n🎵 Spotify AI Playlist Sorter")
    print("─" * 40)

    missing = []
    if SPOTIFY_CLIENT_ID == "YOUR_CLIENT_ID":        missing.append("SPOTIFY_CLIENT_ID")
    if SPOTIFY_CLIENT_SECRET == "YOUR_CLIENT_SECRET": missing.append("SPOTIFY_CLIENT_SECRET")
    if ANTHROPIC_API_KEY == "YOUR_ANTHROPIC_KEY":    missing.append("ANTHROPIC_API_KEY")

    if missing:
        print("⚠️  Missing environment variables:")
        for m in missing:
            print(f"   export {m}=your_value_here")
        print("\nSee README.md for setup instructions.")
        print("─" * 40)

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    base_url = RENDER_URL or f"http://127.0.0.1:{PORT}"
    print(f"🚀 Server running at {base_url}")
    print("   Opening browser...\n")
    if not RENDER_URL:
        threading.Timer(1, lambda: webbrowser.open(base_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Stopped.")


if __name__ == "__main__":
    main()