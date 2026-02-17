import os
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool


async def get_db():
    pool = await get_pool()
    return await pool.acquire()


async def release_db(conn):
    pool = await get_pool()
    await pool.release(conn)


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
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
