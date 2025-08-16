# create_tables.py
import psycopg2
from psycopg2 import sql

# ===== CONFIG =====
DB_NAME = "workout_suggestor"
DB_USER = "postgres"
DB_PASSWORD = "5h0rt5tack%M%"
DB_HOST = "localhost"
DB_PORT = "5432"

# ===== DDL SCRIPT =====
TABLES = [
    """
    CREATE TABLE IF NOT EXISTS exercises (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT,
        primary_muscles TEXT[],
        secondary_muscles TEXT[],
        equipment TEXT[],
        difficulty TEXT,
        instructions TEXT,
        source TEXT,
        source_url TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS media (
        id SERIAL PRIMARY KEY,
        exercise_id INT REFERENCES exercises(id) ON DELETE CASCADE,
        media_type TEXT,
        url TEXT NOT NULL,
        thumbnail_url TEXT,
        order_index INT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tags (
        id SERIAL PRIMARY KEY,
        exercise_id INT REFERENCES exercises(id) ON DELETE CASCADE,
        tag TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sources (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        url TEXT,
        api_used BOOLEAN,
        license TEXT
    );
    """
]

def create_tables():
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        for ddl in TABLES:
            cur.execute(ddl)
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Tables created successfully.")
        
    except Exception as e:
        print("❌ Error creating tables:", e)

if __name__ == "__main__":
    create_tables()
