import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

from auth import get_access_token


# ============================================================
# SETTINGS
# ============================================================

API_URL = "https://graphql.anilist.co"

REQUESTS_PER_BATCH = 29
COOLDOWN_SECONDS = 60

# First test:
# 3 successful additions and then stop.
#
# Change to None after testing:
TEST_ADD_LIMIT = None

FORMATS = [
    "TV",
    "TV_SHORT",
    "MOVIE",
    "SPECIAL",
    "OVA",
    "ONA",
    "MUSIC",
]

DB_FILE = "database.sqlite3"


# ============================================================
# GRAPHQL QUERIES
# ============================================================

VIEWER_QUERY = """
query {
    Viewer {
        id
        name
    }
}
"""


# Official MediaListCollection structure:
# type + userId are required.
MEDIA_LIST_QUERY = """
query ($type: MediaType!, $userId: Int!) {
    MediaListCollection(
        type: $type
        userId: $userId
    ) {
        lists {
            name
            entries {
                mediaId
            }
        }
    }
}
"""


MEDIA_QUERY = """
query ($page: Int!, $format: MediaFormat!) {
    Page(
        page: $page
        perPage: 50
    ) {
        pageInfo {
            currentPage
            lastPage
            hasNextPage
        }

        media(
            type: ANIME
            format: $format
            sort: ID
        ) {
            id

            format

            title {
                romaji
                english
                native
            }

            genres

            tags {
                name
                rank
            }

            studios {
                nodes {
                    id
                    name
                }
            }
        }
    }
}
"""


ADD_MEDIA_MUTATION = """
mutation ($mediaId: Int!, $status: MediaListStatus!) {
    SaveMediaListEntry(
        mediaId: $mediaId
        status: $status
    ) {
        id
        mediaId
        status
    }
}
"""


# ============================================================
# DATABASE
# ============================================================

def init_database():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY,
            title TEXT,
            format TEXT,
            genres TEXT,
            tags TEXT,
            studios TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS added_media (
            media_id INTEGER PRIMARY KEY
        )
    """)

    conn.commit()

    return conn


def cache_media(conn, media):
    title_data = media.get("title") or {}

    title = (
        title_data.get("english")
        or title_data.get("romaji")
        or title_data.get("native")
        or "Unknown"
    )

    genres = json.dumps(
        media.get("genres", []),
        ensure_ascii=False
    )

    tags = json.dumps(
        [
            {
                "name": tag.get("name"),
                "rank": tag.get("rank")
            }
            for tag in media.get("tags", [])
        ],
        ensure_ascii=False
    )

    studios = json.dumps(
        [
            {
                "id": studio.get("id"),
                "name": studio.get("name")
            }
            for studio in (
                media.get("studios", {}).get("nodes", [])
            )
        ],
        ensure_ascii=False
    )

    conn.execute(
        """
        INSERT OR REPLACE INTO media
        (
            id,
            title,
            format,
            genres,
            tags,
            studios
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            media["id"],
            title,
            media.get("format"),
            genres,
            tags,
            studios
        )
    )


def mark_added(conn, media_id):
    conn.execute(
        """
        INSERT OR IGNORE INTO added_media (media_id)
        VALUES (?)
        """,
        (media_id,)
    )


def already_added(conn, media_id):
    cursor = conn.execute(
        """
        SELECT 1
        FROM added_media
        WHERE media_id = ?
        LIMIT 1
        """,
        (media_id,)
    )

    return cursor.fetchone() is not None


# ============================================================
# PROGRESS
# ============================================================

def progress_file(media_format):
    return f"progress_{media_format}.txt"


def load_progress(media_format):
    filename = progress_file(media_format)

    if not os.path.exists(filename):
        return 1

    try:
        with open(filename, "r") as file:
            value = file.read().strip()

        page = int(value)

        if page < 1:
            return 1

        return page

    except (ValueError, OSError):
        return 1


def save_progress(media_format, next_page):
    filename = progress_file(media_format)

    temp_file = filename + ".tmp"

    with open(temp_file, "w") as file:
        file.write(str(next_page))

    os.replace(temp_file, filename)


# ============================================================
# REQUEST LIMITER
# ============================================================

class RequestLimiter:

    def __init__(self):
        self.requests = 0

    def before_request(self):
        if self.requests < REQUESTS_PER_BATCH:
            return

        print()

        for remaining in range(
            COOLDOWN_SECONDS,
            0,
            -1
        ):
            print(
                f"\rNext batch in: {remaining:02d}s",
                end="",
                flush=True
            )

            time.sleep(1)

        print(
            "\rNext batch in: 00s"
        )

        self.requests = 0

    def after_request(self):
        self.requests += 1


# ============================================================
# GRAPHQL REQUEST
# ============================================================

def graphql_request(
    token,
    query,
    variables=None,
    limiter=None
):
    if limiter is not None:
        limiter.before_request()

    payload = json.dumps({
        "query": query,
        "variables": variables or {}
    }).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "AniList-Mass-Adder/1.0",
        }
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            raw = response.read().decode("utf-8")

            data = json.loads(raw)

    except urllib.error.HTTPError as error:

        body = error.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"HTTP {error.code}: {body}"
        )

    except urllib.error.URLError as error:

        raise RuntimeError(
            f"Network error: {error.reason}"
        )

    finally:

        if limiter is not None:
            limiter.after_request()

    if "errors" in data:

        raise RuntimeError(
            json.dumps(
                data["errors"],
                ensure_ascii=False
            )
        )

    if "data" not in data:

        raise RuntimeError(
            "AniList returned no data."
        )

    return data["data"]


# ============================================================
# VIEWER
# ============================================================

def get_viewer(token, limiter):

    data = graphql_request(
        token,
        VIEWER_QUERY,
        limiter=limiter
    )

    viewer = data.get("Viewer")

    if not viewer:
        raise RuntimeError(
            "AniList did not return the authenticated user."
        )

    return viewer


# ============================================================
# LOAD EXISTING ANIME
# ============================================================

def load_existing_media(
    token,
    user_id,
    conn,
    limiter
):
    print("Loading existing AniList entries...")

    data = graphql_request(
        token,
        MEDIA_LIST_QUERY,
        {
            "type": "ANIME",
            "userId": user_id
        },
        limiter
    )

    collection = data.get(
        "MediaListCollection"
    )

    if not collection:
        print("AniList returned an empty list.")
        return 0

    count = 0

    for media_list in collection.get(
        "lists",
        []
    ):

        for entry in media_list.get(
            "entries",
            []
        ):

            media_id = entry.get("mediaId")

            if media_id is None:
                continue

            mark_added(
                conn,
                media_id
            )

            count += 1

    conn.commit()

    print(
        f"Loaded {count} existing entries."
    )

    return count


# ============================================================
# FETCH ANIME PAGE
# ============================================================

def fetch_page(
    token,
    media_format,
    page,
    limiter
):

    data = graphql_request(
        token,
        MEDIA_QUERY,
        {
            "page": page,
            "format": media_format
        },
        limiter
    )

    page_data = data.get("Page")

    if not page_data:
        raise RuntimeError(
            "AniList returned no Page data."
        )

    return page_data


# ============================================================
# ADD ANIME
# ============================================================

def add_media(
    token,
    media_id,
    limiter
):

    return graphql_request(
        token,
        ADD_MEDIA_MUTATION,
        {
            "mediaId": media_id,
            "status": "PLANNING"
        },
        limiter
    )


# ============================================================
# TITLE
# ============================================================

def get_title(media):

    title = media.get("title") or {}

    return (
        title.get("english")
        or title.get("romaji")
        or title.get("native")
        or "Unknown"
    )


# ============================================================
# FORMAT SELECTION
# ============================================================

def choose_formats():

    print()
    print("Available formats:")
    print()

    for index, media_format in enumerate(
        FORMATS,
        start=1
    ):
        print(
            f"{index}. {media_format}"
        )

    print()
    print(
        "Enter numbers separated by commas."
    )
    print(
        "Example: 1,5,6"
    )
    print()

    while True:

        value = input(
            "Formats: "
        ).strip()

        try:

            numbers = [
                int(item.strip())
                for item in value.split(",")
                if item.strip()
            ]

            selected = []

            for number in numbers:

                if not (
                    1 <= number <= len(FORMATS)
                ):
                    raise ValueError

                media_format = FORMATS[
                    number - 1
                ]

                if media_format not in selected:
                    selected.append(
                        media_format
                    )

            if selected:
                return selected

        except ValueError:
            pass

        print(
            "Invalid selection."
        )


# ============================================================
# RUN FORMAT
# ============================================================

def run_format(
    token,
    conn,
    limiter,
    media_format
):

    page = load_progress(
        media_format
    )

    added_count = 0

    print()
    print("=" * 50)
    print(
        f"Format: {media_format}"
    )
    print(
        f"Starting page: {page}"
    )
    print("=" * 50)

    while True:

        print(
            f"\rFormat: {media_format} | "
            f"Page: {page} | "
            f"Requests: "
            f"{limiter.requests}/{REQUESTS_PER_BATCH}",
            end="",
            flush=True
        )

        page_data = fetch_page(
            token,
            media_format,
            page,
            limiter
        )

        media_list = page_data.get(
            "media",
            []
        )

        if not media_list:

            print()
            print(
                f"No media found on page {page}."
            )

            save_progress(
                media_format,
                page
            )

            break

        for media in media_list:

            media_id = media["id"]

            # Cache metadata locally.
            cache_media(
                conn,
                media
            )

            # Already in user's list?
            if already_added(
                conn,
                media_id
            ):
                continue

            # Add to AniList.
            add_media(
                token,
                media_id,
                limiter
            )

            # Only mark it after successful mutation.
            mark_added(
                conn,
                media_id
            )

            conn.commit()

            added_count += 1

            print()
            print(
                f"Added: {get_title(media)}"
            )

            # Test limit.
            if (
                TEST_ADD_LIMIT is not None
                and added_count >= TEST_ADD_LIMIT
            ):

                # Current page is NOT completed.
                # Save the current page so the user
                # can inspect the test result first.
                save_progress(
                    media_format,
                    page
                )

                print()
                print(
                    f"Test limit reached: "
                    f"{TEST_ADD_LIMIT}"
                )

                return

        # Current page completely processed.
        page += 1

        # Only store the next page number.
        save_progress(
            media_format,
            page
        )

        if not page_data["pageInfo"].get(
            "hasNextPage",
            False
        ):

            print()
            print(
                f"Finished {media_format}."
            )

            break


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "╔══════════════════════════════════════════╗"
    )
    print(
        "║          AniList Mass Adder              ║"
    )
    print(
        "╚══════════════════════════════════════════╝"
    )
    print()

    conn = None

    try:

        # OAuth handled by auth.py.
        token = get_access_token()

        print()
        print(
            "Connecting to AniList..."
        )

        conn = init_database()

        limiter = RequestLimiter()

        viewer = get_viewer(
            token,
            limiter
        )

        print()
        print(
            f"Logged in as: {viewer['name']}"
        )

        load_existing_media(
            token,
            viewer["id"],
            conn,
            limiter
        )

        selected_formats = choose_formats()

        print()
        print(
            "Selected:",
            ", ".join(selected_formats)
        )

        for media_format in selected_formats:

            run_format(
                token,
                conn,
                limiter,
                media_format
            )

        print()
        print("Done.")

    except KeyboardInterrupt:

        print()
        print()
        print(
            "Stopped by user."
        )
        print(
            "Progress has been saved."
        )

    except Exception as error:

        print()
        print("ERROR")
        print(error)

        sys.exit(1)

    finally:

        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()