import os
import json
import time
import requests
from flask import Flask, redirect, request, session, jsonify, render_template
from urllib.parse import urlencode
import anthropic

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")

# ── Spotify config ────────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI  = os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:5000/callback")
SPOTIFY_SCOPES        = "user-library-read playlist-modify-public playlist-modify-private"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login")
def login():
    params = urlencode({
        "client_id":     SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  SPOTIFY_REDIRECT_URI,
        "scope":         SPOTIFY_SCOPES,
        "show_dialog":   "true",
    })
    return redirect(f"https://accounts.spotify.com/authorize?{params}")

@app.route("/callback")
def callback():
    error = request.args.get("error")
    if error:
        return f"Spotify auth error: {error}", 400

    code = request.args.get("code")
    token_res = requests.post("https://accounts.spotify.com/api/token", data={
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  SPOTIFY_REDIRECT_URI,
        "client_id":     SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET,
    })
    tokens = token_res.json()
    session["access_token"]  = tokens.get("access_token")
    session["refresh_token"] = tokens.get("refresh_token")
    return redirect("/app")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/app")
def spa():
    if not session.get("access_token"):
        return redirect("/")
    return render_template("app.html")

# ── Spotify helpers ───────────────────────────────────────────────────────────

def spotify_get(path, token, params=None):
    r = requests.get(
        f"https://api.spotify.com/v1{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
    )
    r.raise_for_status()
    return r.json()

def fetch_all_liked(token):
    tracks, url = [], "https://api.spotify.com/v1/me/tracks?limit=50"
    while url:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        data = r.json()
        for item in data["items"]:
            t = item["track"]
            if t:
                tracks.append({
                    "id":      t["id"],
                    "name":    t["name"],
                    "artists": [a["name"] for a in t["artists"]],
                    "album":   t["album"]["name"],
                    "added_at": item["added_at"],
                    "popularity": t.get("popularity", 0),
                    "preview_url": t.get("preview_url"),
                    "image": t["album"]["images"][0]["url"] if t["album"]["images"] else None,
                })
        url = data["next"]
        time.sleep(0.05)
    return tracks

def fetch_audio_features(track_ids, token):
    features = {}
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i+100]
        data = spotify_get("/audio-features", token, {"ids": ",".join(batch)})
        for f in (data.get("audio_features") or []):
            if f:
                features[f["id"]] = {
                    "tempo":        round(f.get("tempo", 0)),
                    "energy":       round(f.get("energy", 0), 2),
                    "valence":      round(f.get("valence", 0), 2),
                    "danceability": round(f.get("danceability", 0), 2),
                    "acousticness": round(f.get("acousticness", 0), 2),
                    "instrumentalness": round(f.get("instrumentalness", 0), 2),
                }
        time.sleep(0.1)
    return features

# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/me")
def api_me():
    token = session.get("access_token")
    if not token:
        return jsonify({"error": "not authenticated"}), 401
    data = spotify_get("/me", token)
    return jsonify({"name": data.get("display_name"), "image": (data.get("images") or [{}])[0].get("url")})

@app.route("/api/liked-songs")
def api_liked_songs():
    token = session.get("access_token")
    if not token:
        return jsonify({"error": "not authenticated"}), 401
    tracks = fetch_all_liked(token)
    session["liked_tracks"] = tracks          # cache for later
    return jsonify({"count": len(tracks), "sample": tracks[:5]})

@app.route("/api/suggest-playlists", methods=["POST"])
def api_suggest():
    token = session.get("access_token")
    if not token:
        return jsonify({"error": "not authenticated"}), 401

    tracks = session.get("liked_tracks")
    if not tracks:
        return jsonify({"error": "fetch liked songs first"}), 400

    # Fetch audio features
    ids = [t["id"] for t in tracks if t["id"]]
    features = fetch_audio_features(ids, token)

    # Enrich tracks
    enriched = []
    for t in tracks:
        f = features.get(t["id"], {})
        enriched.append({**t, **f})

    # Build a compact summary for Claude (max 800 songs to keep prompt manageable)
    sample = enriched[:800]
    summary_lines = []
    for t in sample:
        artists = ", ".join(t["artists"])
        year = t["added_at"][:4] if t.get("added_at") else "?"
        summary_lines.append(
            f'{t["id"]}|{t["name"]}|{artists}|pop:{t.get("popularity",0)}|'
            f'bpm:{t.get("tempo","?")}|energy:{t.get("energy","?")}|'
            f'valence:{t.get("valence","?")}|dance:{t.get("danceability","?")}|'
            f'acoustic:{t.get("acousticness","?")}|added:{year}'
        )

    prompt = f"""You are a music curation expert. A user has {len(tracks)} liked songs on Spotify that are sitting in their library uncurated. Your job is to group them into meaningful playlists they would actually listen to.

Here are their songs (id|name|artists|popularity|bpm|energy|valence|danceability|acousticness|year_added):
{chr(10).join(summary_lines)}

Instructions:
- Create 5–12 playlists that feel like real, listenable collections (not just genre buckets)
- Name each playlist evocatively (e.g. "Late Night Drive", "Sunday Morning Coffee", "Gym Beast Mode")
- Write a short 1-sentence description for each
- Assign each track ID to exactly ONE playlist
- Try to cover all tracks. If a track doesn't fit anywhere well, put it in a catch-all like "Everything Else"
- Base groupings on a combination of: mood/vibe, energy level, tempo, acousticness, era/decade, and artist style

Return ONLY valid JSON in this exact format:
{{
  "playlists": [
    {{
      "name": "Playlist Name",
      "description": "One sentence description.",
      "emoji": "🎵",
      "track_ids": ["id1", "id2", ...]
    }}
  ]
}}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    suggestion = json.loads(raw)

    # Enrich suggestion with track details for preview
    track_map = {t["id"]: t for t in enriched}
    for pl in suggestion["playlists"]:
        pl["tracks"] = [track_map[tid] for tid in pl["track_ids"] if tid in track_map]
        pl["count"]  = len(pl["tracks"])

    session["suggestion"] = suggestion
    return jsonify(suggestion)

@app.route("/api/create-playlists", methods=["POST"])
def api_create():
    token = session.get("access_token")
    if not token:
        return jsonify({"error": "not authenticated"}), 401

    body = request.json  # { playlists: [{name, description, track_ids}] }
    me   = spotify_get("/me", token)
    user_id = me["id"]

    created = []
    for pl in body.get("playlists", []):
        # Create playlist
        r = requests.post(
            f"https://api.spotify.com/v1/users/{user_id}/playlists",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"name": pl["name"], "description": pl.get("description", ""), "public": False}
        )
        r.raise_for_status()
        pl_id = r.json()["id"]

        # Add tracks in batches of 100
        track_uris = [f"spotify:track:{tid}" for tid in pl["track_ids"]]
        for i in range(0, len(track_uris), 100):
            batch = track_uris[i:i+100]
            requests.post(
                f"https://api.spotify.com/v1/playlists/{pl_id}/tracks",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"uris": batch}
            )
            time.sleep(0.1)

        created.append({"name": pl["name"], "id": pl_id, "count": len(track_uris)})

    return jsonify({"created": created})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
