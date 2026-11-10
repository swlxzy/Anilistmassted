import json
import sys
import time
import urllib.request
import urllib.error

API = "https://graphql.anilist.co"

PROFILE_QUERY = """
query ($name: String!) {
  User(name: $name) {
    id
    name
    about
    siteUrl
    avatar { large }
    bannerImage
    createdAt
    updatedAt

    statistics {
      anime { ...Stats }
      manga { ...Stats }
    }
  }
}

fragment Stats on UserStatistics {
  count
  meanScore
  standardDeviation
  minutesWatched
  episodesWatched
  chaptersRead
  volumesRead

  formats(limit: 100) {
    count meanScore format
  }
  genres(limit: 100) {
    count meanScore genre
  }
  lengths(limit: 100) {
    count meanScore length
  }
  releaseYears(limit: 100) {
    count meanScore releaseYear
  }
  startYears(limit: 100) {
    count meanScore startYear
  }
  scores(limit: 100) {
    count meanScore score
  }
  statuses(limit: 100) {
    count meanScore status
  }
  tags(limit: 100) {
    count meanScore
    tag { id name category rank }
  }
  staff(limit: 100) {
    count meanScore
    staff { id name { full native } }
  }
  studios(limit: 100) {
    count meanScore
    studio { id name }
  }
  voiceActors(limit: 100) {
    count meanScore
    voiceActor { id name { full native } }
  }
}
"""

FAVOURITES_QUERY = """
query ($name: String!, $page: Int!) {
  User(name: $name) {
    favourites {
      anime(page: $page, perPage: 25) {
        pageInfo { hasNextPage }
        edges {
          favouriteOrder
          node {
            id
            title { romaji english native }
            type
            format
            siteUrl
          }
        }
      }
      manga(page: $page, perPage: 25) {
        pageInfo { hasNextPage }
        edges {
          favouriteOrder
          node {
            id
            title { romaji english native }
            type
            format
            siteUrl
          }
        }
      }
      characters(page: $page, perPage: 25) {
        pageInfo { hasNextPage }
        edges {
          favouriteOrder
          node {
            id
            name { full native }
            siteUrl
          }
        }
      }
      staff(page: $page, perPage: 25) {
        pageInfo { hasNextPage }
        edges {
          favouriteOrder
          node {
            id
            name { full native }
            siteUrl
          }
        }
      }
      studios(page: $page, perPage: 25) {
        pageInfo { hasNextPage }
        edges {
          favouriteOrder
          node { id name siteUrl }
        }
      }
    }
  }
}
"""


def request(query, variables):
    body = json.dumps({
        "query": query,
        "variables": variables
    }).encode()

    req = urllib.request.Request(
        API,
        data=body,
        headers={
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "AniListAccountExporter/1.0"
         
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.loads(res.read())

    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"HTTP {e.code}: {e.read().decode(errors='replace')}"
        )

    if data.get("errors"):
        raise RuntimeError(
            json.dumps(data["errors"], ensure_ascii=False)
        )

    return data["data"]


def get_favourites(username):
    result = {}
    categories = (
        "anime", "manga", "characters", "staff", "studios"
    )

    page = 1

    while True:
        user = request(
            FAVOURITES_QUERY,
            {"name": username, "page": page}
        )["User"]

        if user is None:
            raise RuntimeError("User not found.")

        favs = user["favourites"]
        has_more = False

        for category in categories:
            connection = favs[category]
            edges = connection["edges"]

            if edges:
                result.setdefault(category, []).extend(
                    {
                        **edge["node"],
                        "favouriteOrder": edge["favouriteOrder"]
                    }
                    for edge in edges
                )

            if connection["pageInfo"]["hasNextPage"]:
                has_more = True

        if not has_more:
            break

        page += 1
        time.sleep(1.1)

    return result


def export(username):
    print(f"[+] Exporting {username}...")

    data = request(
        PROFILE_QUERY,
        {"name": username}
    )["User"]

    if data is None:
        raise RuntimeError("User not found.")

    favourites = get_favourites(username)

    result = {
        "profile": {
            key: data[key]
            for key in (
                "id", "name", "about", "siteUrl",
                "avatar", "bannerImage",
                "createdAt", "updatedAt"
            )
        },
        "statistics": data["statistics"]
    }

    if favourites:
        result["favourites"] = favourites

    filename = f"{username}_account.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(
            result,
            f,
            ensure_ascii=False,
            separators=(",", ":")
        )

    print(f"[+] Saved: {filename}")
    print(f"[+] Size: {len(json.dumps(result, ensure_ascii=False, separators=(',', ':')))} bytes")


if __name__ == "__main__":
    username = (
        sys.argv[1]
        if len(sys.argv) > 1
        else input("AniList username: ").strip()
    )

    if not username:
        sys.exit("Username required.")

    try:
        export(username)
    except Exception as e:
        sys.exit(f"[!] {e}")