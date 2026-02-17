import os
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")

_initialized = False


async def get_db():
    """Get a fresh connection for each request (serverless-friendly)."""
    conn = await asyncpg.connect(DATABASE_URL)
    return conn


async def release_db(conn):
    """Close the connection after use."""
    await conn.close()


async def ensure_tables():
    """Create tables if they don't exist. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                image_url TEXT,
                access_token TEXT,
                refresh_token TEXT,
                token_expires_at DOUBLE PRECISION
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shared_songs (
                id SERIAL PRIMARY KEY,
                from_user_id TEXT NOT NULL REFERENCES users(id),
                to_user_id TEXT NOT NULL REFERENCES users(id),
                track_id TEXT NOT NULL,
                track_name TEXT,
                artist_name TEXT,
                album_image TEXT,
                preview_url TEXT,
                spotify_url TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                id SERIAL PRIMARY KEY,
                shared_song_id INTEGER NOT NULL REFERENCES shared_songs(id),
                user_id TEXT NOT NULL REFERENCES users(id),
                reaction TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(shared_song_id, user_id)
            );
        """)
        _initialized = True
    finally:
        await conn.close()
