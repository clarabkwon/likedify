# Sortify
Your Spotify liked songs, finally organized — powered by Claude AI.

---

## What it does

1. You log in with Spotify
2. Sortify fetches all your liked songs + their audio data (tempo, energy, mood, etc.)
3. Claude AI groups them into real, listenable playlists ("Late Night Drive", "Sunday Morning Coffee", etc.)
4. You review, rename, or remove playlists before anything is created
5. Hit one button and the playlists appear in your Spotify

---

## Setup (step by step)

### Step 1 — Get your Spotify credentials

1. Go to https://developer.spotify.com/dashboard
2. Log in with your Spotify account
3. Click "Create app"
4. Fill in:
   - App name: Sortify (or anything)
   - App description: anything
   - Redirect URI: http://localhost:5000/callback
   - Check "Web API"
5. Click Save
6. On the app page, click Settings — you'll see your Client ID and Client Secret

---

### Step 2 — Get your Anthropic API key

1. Go to https://console.anthropic.com
2. Sign up / log in
3. Go to API Keys → Create Key
4. Copy the key (starts with sk-ant-...)

Note: The API costs a small amount per run (~$0.01–0.05 depending on library size). You'll need to add a payment method.

---

### Step 3 — Install Python and dependencies

Make sure Python 3.9+ is installed: https://python.org/downloads

Open a terminal in the project folder, then run:

    pip install -r requirements.txt

---

### Step 4 — Configure your keys

Copy the example env file:

    cp .env.example .env

Open .env in any text editor and fill in:

    SPOTIFY_CLIENT_ID=your_client_id
    SPOTIFY_CLIENT_SECRET=your_client_secret
    SPOTIFY_REDIRECT_URI=http://localhost:5000/callback
    ANTHROPIC_API_KEY=sk-ant-...
    FLASK_SECRET_KEY=any-random-string-you-choose

---

### Step 5 — Run the app

    python app.py

Open your browser at: http://localhost:5000

---

## Deploying for others (e.g. on Railway or Render)

1. Push this folder to a GitHub repo
2. Connect to Railway (https://railway.app) or Render (https://render.com)
3. Add the same environment variables in the platform dashboard
4. Change SPOTIFY_REDIRECT_URI to your live URL, e.g. https://sortify.up.railway.app/callback
5. Update the Redirect URI in your Spotify app settings to match
6. Deploy!

For public deployment, Spotify requires you to add users to an allowlist in the developer portal until you apply for quota extension. Go to your app → Users and Access → Add users.

---

## Notes

- Your liked songs are never stored — they only live in your browser session
- Playlists are created as private by default
- Songs with less than 30 seconds or local files may be skipped by Spotify's API
- If you have 1000+ liked songs, the AI call may take up to 60 seconds
