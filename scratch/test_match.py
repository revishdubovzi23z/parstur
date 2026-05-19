import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sync_kinopub import SCORE_MIN_ACCEPT, score_candidate

# Let's mock the par2 item
item = {
    "id": 5426,
    "title": "Ночная смена / Last Straw (2024)",
    "year": 2024,
    "category_id": 16,
    "kp_id": "5379715",
    "imdb_id": "tt24249072",
}

# The candidate from kino.pub
candidate = {
    "id": 104929,
    "title": "Ночная смена / Last Straw",
    "type": "movie",
    "year": 2023,  # Wait! What year does kino.pub return? Let's check!
    "kinopoisk": "5379715",
    "imdb": "24249072",
}

score = score_candidate(item=item, candidate=candidate, type_hint="movie")
print(f"Mocked score (with KP matches): {score}")
print(f"Accept threshold: {SCORE_MIN_ACCEPT}")
