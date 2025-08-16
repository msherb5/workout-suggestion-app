# scrape_exrx.py
import time
import re
from typing import List, Optional
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import psycopg2
from psycopg2.extras import execute_values

# ------------------ Config ------------------
DB_NAME = "workout_suggestor"
DB_USER = "postgres"
DB_PASS = "5h0rt5tack%M%"
DB_HOST = "localhost"
DB_PORT = "5432"

BASE_URL = "https://exrx.net"
DIRECTORY_URL = "https://exrx.net/Lists/Directory"
SOURCE_NAME = "ExRx.net"

# Politeness / limits while testing
REQUEST_DELAY_SEC = 0.8
LIMIT_GROUPS = 3        # set to None for all groups
LIMIT_EXERCISES = 20    # set to None for all exercises per group

# ------------------ Helpers ------------------
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; WOS-Scraper/1.0; +https://example.com)"
})

def get_soup(url: str) -> BeautifulSoup:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")

def clean_text(txt: Optional[str]) -> Optional[str]:
    if not txt:
        return None
    return re.sub(r"\s+", " ", txt).strip()

def to_array(value: Optional[str]) -> List[str]:
    # Equipment is often "Barbell; Bench" or "Dumbbell, Bench" or a line of text
    if not value:
        return []
    # split on commas/semicolons and clean
    parts = re.split(r"[;,/]", value)
    return [clean_text(p) for p in parts if clean_text(p)]

def next_text_after_label(soup: BeautifulSoup, labels: List[str]) -> Optional[str]:
    # Finds a bold/strong/heading containing any label, returns the next text block
    label_re = re.compile("|".join([re.escape(lbl) for lbl in labels]), re.I)
    # Look through various tags that often contain labels
    for tag in soup.find_all(["strong", "b", "th", "h2", "h3"]):
        if tag.get_text(strip=True) and label_re.search(tag.get_text(strip=True)):
            # prefer list/paragraph nearby
            nxt = tag.find_next(["ol", "ul", "p", "td", "div"])
            if nxt:
                return clean_text(nxt.get_text(" ", strip=True))
    return None

def extract_muscles(soup: BeautifulSoup):
    # ExRx often uses "Target" (primary) and "Synergists" (secondary)
    primary = next_text_after_label(soup, ["Target", "Target Muscles"])
    secondary = next_text_after_label(soup, ["Synergists", "Synergist"])
    # Convert to arrays (split if comma/semicolon separated)
    prim_list = to_array(primary) if primary else []
    sec_list = to_array(secondary) if secondary else []
    return prim_list, sec_list

def extract_instructions(soup: BeautifulSoup) -> Optional[str]:
    # Common headings: "Execution", sometimes "Instructions"
    text = next_text_after_label(soup, ["Execution", "Instructions"])
    if text:
        return text
    # Fallback: main content div
    main = soup.find(id="main-content") or soup.find("article")
    return clean_text(main.get_text(" ", strip=True)) if main else None

def extract_equipment(soup: BeautifulSoup) -> List[str]:
    equip = next_text_after_label(soup, ["Equipment"])
    return to_array(equip)

def absolute_media_urls(soup: BeautifulSoup) -> List[str]:
    urls = []
    for img in soup.select("img[src]"):
        src = img.get("src")
        if not src:
            continue
        urls.append(urljoin(BASE_URL, src))
    # (Optional) add videos if present
    for vid in soup.select("video source[src], video[src]"):
        src = vid.get("src")
        if not src:
            continue
        urls.append(urljoin(BASE_URL, src))
    # de-dup
    return list(dict.fromkeys(urls))

# ------------------ DB Bootstrapping ------------------
def ensure_constraints(cur):
    # Make exercises.source_url unique so ON CONFLICT works
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'exercises_source_url_key'
            ) THEN
                ALTER TABLE exercises
                ADD CONSTRAINT exercises_source_url_key UNIQUE (source_url);
            END IF;
        END$$;
    """)

# ------------------ Scrape Flow ------------------
def scrape():
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASS,
        host=DB_HOST, port=DB_PORT
    )
    conn.autocommit = False
    cur = conn.cursor()

    try:
        ensure_constraints(cur)
        conn.commit()

        # 1) Directory → group links
        print(f"Fetching directory: {DIRECTORY_URL}")
        soup = get_soup(DIRECTORY_URL)
        # muscle group pages live under /Lists/
        group_links = []
        for a in soup.select("a[href^='/Lists/']"):
            href = a.get("href")
            text = clean_text(a.get_text())
            # Skip self-link and non-groups
            if not href or "Directory" in href:
                continue
            group_links.append((text, urljoin(BASE_URL, href)))

        # dedup & optionally limit
        seen = set()
        groups = []
        for name, href in group_links:
            if href in seen:
                continue
            seen.add(href)
            groups.append((name, href))
        if LIMIT_GROUPS:
            groups = groups[:LIMIT_GROUPS]

        print(f"Found {len(groups)} groups to crawl")

        # 2) For each group, find exercise links
        for gname, gurl in groups:
            print(f"\n== Group: {gname} -> {gurl}")
            time.sleep(REQUEST_DELAY_SEC)
            g_soup = get_soup(gurl)

            # ExRx exercises often under /WeightExercises/; include other categories defensively
            exercise_anchors = g_soup.select(
                "a[href*='/WeightExercises/'], a[href*='/Plyometrics/'], a[href*='/Stretching/'], a[href*='/Aerobic/']"
            )
            ex_links = []
            for a in exercise_anchors:
                href = a.get("href")
                if not href:
                    continue
                ex_links.append(urljoin(BASE_URL, href))

            # dedup and (optionally) limit per-group
            ex_links = list(dict.fromkeys(ex_links))
            if LIMIT_EXERCISES:
                ex_links = ex_links[:LIMIT_EXERCISES]
            print(f"  -> {len(ex_links)} exercise pages")

            for ex_url in ex_links:
                try:
                    time.sleep(REQUEST_DELAY_SEC)
                    ex_soup = get_soup(ex_url)

                    # Name (h1 is typical)
                    h1 = ex_soup.find("h1")
                    name = clean_text(h1.get_text()) if h1 else None
                    if not name:
                        print(f"    [skip] No name found: {ex_url}")
                        continue

                    # Instructions / Equipment / Muscles
                    instructions = extract_instructions(ex_soup)
                    equipment = extract_equipment(ex_soup)
                    prim_muscles, sec_muscles = extract_muscles(ex_soup)

                    # 3) Insert into exercises
                    cur.execute("""
                        INSERT INTO exercises
                            (name, category, primary_muscles, secondary_muscles,
                             equipment, difficulty, instructions, source, source_url)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (source_url) DO UPDATE
                        SET name = EXCLUDED.name,
                            primary_muscles = EXCLUDED.primary_muscles,
                            secondary_muscles = EXCLUDED.secondary_muscles,
                            equipment = EXCLUDED.equipment,
                            instructions = EXCLUDED.instructions,
                            category = EXCLUDED.category
                        RETURNING id;
                    """, (
                        name,
                        gname,                   # category = group name (roughly)
                        prim_muscles or [],
                        sec_muscles or [],
                        equipment or [],
                        None,                    # difficulty (not consistently on ExRx)
                        instructions,
                        SOURCE_NAME,
                        ex_url
                    ))
                    exercise_id = cur.fetchone()[0]

                    # 4) Insert media
                    media_urls = absolute_media_urls(ex_soup)
                    if media_urls:
                        media_rows = [(exercise_id, "image", u, None, i)
                                      for i, u in enumerate(media_urls)]
                        execute_values(cur, """
                            INSERT INTO media (exercise_id, media_type, url, thumbnail_url, order_index)
                            VALUES %s
                            ON CONFLICT DO NOTHING;
                        """, media_rows)

                    conn.commit()
                    print(f"    [ok] {name} ({len(media_urls)} media)")
                except requests.HTTPError as e:
                    conn.rollback()
                    print(f"    [http] {ex_url} -> {e}")
                except Exception as e:
                    conn.rollback()
                    print(f"    [err] {ex_url} -> {e}")

        cur.close()
        conn.close()
        print("\n✅ Done.")
    except Exception as e:
        conn.rollback()
        cur.close()
        conn.close()
        raise
