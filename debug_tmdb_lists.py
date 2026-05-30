from tmdb_client import TMDBClient

client = TMDBClient()
print(
    "Without params:",
    client.session.get(
        "https://api.themoviedb.org/4/list/8655818", headers=client.headers
    ).status_code,
)
print(
    "With params:",
    client.session.get(
        "https://api.themoviedb.org/4/list/8655818", headers=client.headers, params={"page": 1}
    ).status_code,
)
