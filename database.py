import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "mj.db")


async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    db = await aiosqlite.connect(DB_PATH)
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            display_name TEXT,
            image_url TEXT,
            access_token TEXT,
            refresh_token TEXT,
            token_expires_at REAL
        );

        CREATE TABLE IF NOT EXISTS shared_songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id TEXT NOT NULL,
            to_user_id TEXT NOT NULL,
            track_id TEXT NOT NULL,
            track_name TEXT,
            artist_name TEXT,
            album_image TEXT,
            preview_url TEXT,
            spotify_url TEXT,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (from_user_id) REFERENCES users(id),
            FOREIGN KEY (to_user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shared_song_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            reaction TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(shared_song_id, user_id),
            FOREIGN KEY (shared_song_id) REFERENCES shared_songs(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    await db.commit()
    await db.close()
