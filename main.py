import os
import time
import random
import urllib.parse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer

from database import ensure_tables, get_db, release_db

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")

SCOPES = "user-read-recently-played user-top-read user-library-read"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

serializer = URLSafeSerializer(SECRET_KEY)

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@app.middleware("http")
async def db_init_middleware(request: Request, call_next):
    """Ensure tables exist on first request (lazy init for serverless)."""
    try:
        await ensure_tables()
    except Exception:
        pass  # Tables likely already exist
    response = await call_next(request)
    return response


# --- Auth helpers ---

def make_auth_url():
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "show_dialog": "true",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code(code: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        })
        resp.raise_for_status()
        return resp.json()


async def get_valid_token(user_id: str):
    conn = await get_db()
    try:
        row = await conn.fetchrow(
            "SELECT access_token, refresh_token, token_expires_at FROM users WHERE id = $1", user_id
        )
        if not row:
            return None

        if time.time() > row["token_expires_at"] - 60:
            token_data = await refresh_access_token(row["refresh_token"])
            new_access = token_data["access_token"]
            new_expires = time.time() + token_data["expires_in"]
            new_refresh = token_data.get("refresh_token", row["refresh_token"])
            await conn.execute(
                "UPDATE users SET access_token=$1, refresh_token=$2, token_expires_at=$3 WHERE id=$4",
                new_access, new_refresh, new_expires, user_id
            )
            return new_access

        return row["access_token"]
    finally:
        await release_db(conn)


async def spotify_get(token: str, endpoint: str, params: dict = None):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_BASE}/{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


def get_current_user_id(request: Request):
    session = request.cookies.get("session")
    if not session:
        return None
    try:
        return serializer.loads(session)
    except Exception:
        return None


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        return templates.TemplateResponse("index.html", {"request": request, "logged_in": False})

    conn = await get_db()
    try:
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not row:
            return templates.TemplateResponse("index.html", {"request": request, "logged_in": False})
        return templates.TemplateResponse("index.html", {
            "request": request,
            "logged_in": True,
            "user": dict(row),
        })
    finally:
        await release_db(conn)


@app.get("/login")
async def login():
    return RedirectResponse(make_auth_url())


@app.get("/callback")
async def callback(request: Request, code: str = None, error: str = None):
    if error:
        return RedirectResponse("/?error=" + error)

    token_data = await exchange_code(code)
    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    expires_at = time.time() + token_data["expires_in"]

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_BASE}/me", headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        profile = resp.json()

    user_id = profile["id"]
    display_name = profile.get("display_name", user_id)
    images = profile.get("images", [])
    image_url = images[0]["url"] if images else ""

    conn = await get_db()
    try:
        await conn.execute("""
            INSERT INTO users (id, display_name, image_url, access_token, refresh_token, token_expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT(id) DO UPDATE SET
                display_name=EXCLUDED.display_name,
                image_url=EXCLUDED.image_url,
                access_token=EXCLUDED.access_token,
                refresh_token=EXCLUDED.refresh_token,
                token_expires_at=EXCLUDED.token_expires_at
        """, user_id, display_name, image_url, access_token, refresh_token, expires_at)
    finally:
        await release_db(conn)

    response = RedirectResponse("/")
    session_token = serializer.dumps(user_id)
    response.set_cookie("session", session_token, httponly=True, max_age=60 * 60 * 24 * 30)
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/")
    response.delete_cookie("session")
    return response


# --- API routes ---

@app.get("/api/users")
async def api_users(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)
    conn = await get_db()
    try:
        rows = await conn.fetch("SELECT id, display_name, image_url FROM users WHERE id != $1", user_id)
        return [dict(r) for r in rows]
    finally:
        await release_db(conn)


@app.get("/api/me/top-tracks")
async def my_top_tracks(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)
    token = await get_valid_token(user_id)
    data = await spotify_get(token, "me/top/tracks", {"limit": 50, "time_range": "medium_term"})
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "artist": ", ".join(a["name"] for a in t["artists"]),
            "album_image": t["album"]["images"][0]["url"] if t["album"]["images"] else "",
            "preview_url": t.get("preview_url"),
            "spotify_url": t["external_urls"]["spotify"],
        }
        for t in data["items"]
    ]


@app.get("/api/recently-played")
async def all_recently_played(request: Request):
    """Fetch recently played tracks for ALL users, merged and sorted by time."""
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)

    # Get all users from the database
    conn = await get_db()
    try:
        rows = await conn.fetch("SELECT id, display_name FROM users")
    finally:
        await release_db(conn)

    all_tracks = []
    for row in rows:
        uid = row["id"]
        name = row["display_name"]
        try:
            token = await get_valid_token(uid)
            if not token:
                continue
            data = await spotify_get(token, "me/player/recently-played", {"limit": 20})
            for item in data["items"]:
                all_tracks.append({
                    "id": item["track"]["id"],
                    "name": item["track"]["name"],
                    "artist": ", ".join(a["name"] for a in item["track"]["artists"]),
                    "album_image": item["track"]["album"]["images"][0]["url"] if item["track"]["album"]["images"] else "",
                    "spotify_url": item["track"]["external_urls"]["spotify"],
                    "played_at": item["played_at"],
                    "user_id": uid,
                    "user_name": name,
                })
        except Exception:
            continue

    # Sort by played_at descending (most recent first) and take top 20
    all_tracks.sort(key=lambda x: x["played_at"], reverse=True)
    return all_tracks[:20]


@app.get("/api/me/top-artists")
async def my_top_artists(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)
    token = await get_valid_token(user_id)
    data = await spotify_get(token, "me/top/artists", {"limit": 20, "time_range": "short_term"})
    return [
        {
            "id": a["id"],
            "name": a["name"],
            "genres": a["genres"],
            "image": a["images"][0]["url"] if a["images"] else "",
        }
        for a in data["items"]
    ]


@app.get("/api/compare/{other_user_id}")
async def compare(request: Request, other_user_id: str):
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)

    my_token = await get_valid_token(user_id)
    other_token = await get_valid_token(other_user_id)
    if not other_token:
        raise HTTPException(404, "Other user not found or not connected")

    my_top = await spotify_get(my_token, "me/top/artists", {"limit": 50, "time_range": "medium_term"})
    other_top = await spotify_get(other_token, "me/top/artists", {"limit": 50, "time_range": "medium_term"})

    my_artists = {a["id"]: a["name"] for a in my_top["items"]}
    other_artists = {a["id"]: a["name"] for a in other_top["items"]}

    shared = set(my_artists.keys()) & set(other_artists.keys())

    my_tracks = await spotify_get(my_token, "me/top/tracks", {"limit": 50, "time_range": "medium_term"})
    other_tracks = await spotify_get(other_token, "me/top/tracks", {"limit": 50, "time_range": "medium_term"})

    my_track_ids = {t["id"] for t in my_tracks["items"]}
    other_track_ids = {t["id"] for t in other_tracks["items"]}
    shared_tracks = my_track_ids & other_track_ids

    total_possible = max(len(my_artists) + len(other_artists), 1)
    compatibility = round(len(shared) / (total_possible / 2) * 100, 1)
    compatibility = min(compatibility, 100)

    # --- Build combined ranked track list ---
    # Index my tracks: id -> {rank, track info}
    all_tracks = {}
    for i, t in enumerate(my_tracks["items"]):
        tid = t["id"]
        all_tracks[tid] = {
            "id": tid,
            "name": t["name"],
            "artist": ", ".join(a["name"] for a in t["artists"]),
            "album_image": t["album"]["images"][0]["url"] if t["album"]["images"] else "",
            "spotify_url": t["external_urls"]["spotify"],
            "my_rank": i + 1,
            "their_rank": None,
        }

    # Merge other user's tracks
    for i, t in enumerate(other_tracks["items"]):
        tid = t["id"]
        if tid in all_tracks:
            # Track exists in both lists
            all_tracks[tid]["their_rank"] = i + 1
        else:
            all_tracks[tid] = {
                "id": tid,
                "name": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "album_image": t["album"]["images"][0]["url"] if t["album"]["images"] else "",
                "spotify_url": t["external_urls"]["spotify"],
                "my_rank": None,
                "their_rank": i + 1,
            }

    # Score and sort: shared songs get average rank, solo songs get rank + 25 penalty
    for tid, info in all_tracks.items():
        if info["my_rank"] and info["their_rank"]:
            info["score"] = (info["my_rank"] + info["their_rank"]) / 2
            info["shared"] = True
        elif info["my_rank"]:
            info["score"] = info["my_rank"] + 25
            info["shared"] = False
        else:
            info["score"] = info["their_rank"] + 25
            info["shared"] = False

    combined_list = sorted(all_tracks.values(), key=lambda x: x["score"])

    # Assign combined rank
    for i, track in enumerate(combined_list):
        track["combined_rank"] = i + 1
        del track["score"]  # Don't send internal score to frontend

    return {
        "compatibility_score": compatibility,
        "shared_artists": [my_artists[aid] for aid in shared],
        "shared_track_count": len(shared_tracks),
        "my_top_genres": _top_genres(my_top["items"]),
        "their_top_genres": _top_genres(other_top["items"]),
        "combined_tracks": combined_list,
    }


def _top_genres(artists):
    from collections import Counter
    genres = Counter()
    for a in artists:
        for g in a.get("genres", []):
            genres[g] += 1
    return [g for g, _ in genres.most_common(10)]


@app.get("/api/search")
async def search_tracks(request: Request, q: str):
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)
    token = await get_valid_token(user_id)
    data = await spotify_get(token, "search", {"q": q, "type": "track", "limit": 10})
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "artist": ", ".join(a["name"] for a in t["artists"]),
            "album_image": t["album"]["images"][0]["url"] if t["album"]["images"] else "",
            "preview_url": t.get("preview_url"),
            "spotify_url": t["external_urls"]["spotify"],
        }
        for t in data["tracks"]["items"]
    ]


@app.post("/api/share")
async def share_song(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)
    body = await request.json()
    conn = await get_db()
    try:
        await conn.execute("""
            INSERT INTO shared_songs (from_user_id, to_user_id, track_id, track_name, artist_name, album_image, preview_url, spotify_url, message)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
            user_id,
            body["to_user_id"],
            body["track_id"],
            body["track_name"],
            body["artist_name"],
            body.get("album_image", ""),
            body.get("preview_url", ""),
            body.get("spotify_url", ""),
            body.get("message", ""),
        )
    finally:
        await release_db(conn)
    return {"ok": True}


@app.get("/api/shared")
async def get_shared(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)
    conn = await get_db()
    try:
        rows = await conn.fetch("""
            SELECT s.*, u.display_name as from_name,
                   (SELECT reaction FROM reactions WHERE shared_song_id = s.id AND user_id = $1) as my_reaction
            FROM shared_songs s
            JOIN users u ON u.id = s.from_user_id
            WHERE s.to_user_id = $2 OR s.from_user_id = $3
            ORDER BY s.created_at DESC
        """, user_id, user_id, user_id)
        return [dict(r) for r in rows]
    finally:
        await release_db(conn)


@app.post("/api/react")
async def react_to_song(request: Request):
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)
    body = await request.json()
    conn = await get_db()
    try:
        await conn.execute("""
            INSERT INTO reactions (shared_song_id, user_id, reaction)
            VALUES ($1, $2, $3)
            ON CONFLICT(shared_song_id, user_id) DO UPDATE SET reaction=EXCLUDED.reaction
        """, body["shared_song_id"], user_id, body["reaction"])
    finally:
        await release_db(conn)
    return {"ok": True}


@app.get("/api/me/top-lyrics")
async def my_top_lyrics(request: Request):
    """Fetch random lyrics lines from the user's top tracks using lyrics.ovh."""
    import asyncio

    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(401)
    token = await get_valid_token(user_id)
    data = await spotify_get(token, "me/top/tracks", {"limit": 50, "time_range": "medium_term"})

    # Pick 15 random tracks to keep within Vercel's time limit
    tracks = data["items"]
    random.shuffle(tracks)
    tracks = tracks[:15]

    async def fetch_lyric(client, track_name, artist_name):
        clean_name = track_name.split(" (")[0].split(" -")[0].split(" feat")[0].split(" ft.")[0].strip()
        try:
            resp = await client.get(
                f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist_name)}/{urllib.parse.quote(clean_name)}"
            )
            if resp.status_code == 200:
                lyrics_text = resp.json().get("lyrics", "")
                if lyrics_text:
                    lines = [
                        line.strip() for line in lyrics_text.split("\n")
                        if line.strip()
                        and len(line.strip()) > 10
                        and not line.strip().startswith("[")
                        and not line.strip().startswith("(")
                        and "Paroles de" not in line
                    ]
                    if lines:
                        return {
                            "text": random.choice(lines),
                            "attr": f"{track_name} \u2014 {artist_name}",
                        }
        except Exception:
            pass
        return None

    # Fire requests in parallel, hard cap at 7 seconds total
    async with httpx.AsyncClient(timeout=4.0) as client:
        tasks = []
        for t in tracks:
            artist_name = t["artists"][0]["name"] if t["artists"] else "Unknown"
            tasks.append(fetch_lyric(client, t["name"], artist_name))

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=7.0
            )
        except asyncio.TimeoutError:
            results = []

    lyrics_quotes = [r for r in results if r and not isinstance(r, Exception)]
    random.shuffle(lyrics_quotes)
    return lyrics_quotes


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8888))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
