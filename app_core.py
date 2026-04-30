import re
import unicodedata


def normalize_title(title):
    if not title:
        return ""
    t = str(title).lower()
    t = t.replace("x", "х")
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"\[.*?\]", "", t)
    t = re.sub(r"[^a-zа-яё0-9]", "", t)
    t = unicodedata.normalize("NFC", t)
    return t.strip()


def clean_title_for_search(title):
    if not title:
        return ""
    t = str(title).lower()
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"\[.*?\]", "", t)
    t = re.sub(r"[^a-zа-яё0-9\s]", " ", t)
    t = " ".join(t.split())
    return t.strip()


RUTOR_CATEGORIES = {
    0: "Любая категория",
    -1: "Все видео",
    1: "Зарубежные фильмы",
    5: "Наши фильмы",
    12: "Научно-популярные фильмы",
    4: "Зарубежные сериалы",
    16: "Наши сериалы",
    6: "Телевизор",
    7: "Мультипликация",
    10: "Аниме",
    8: "Игры",
    13: "Спорт и Здоровье",
    15: "Юмор",
    3: "Другое",
}

VIDEO_CATEGORY_IDS = (1, 4, 5, 16, 7)


class TrackerAppCore:
    def __init__(self, db_path="app_data.db"):
        self.db_path = db_path
        from db import Database

        self.db = Database(db_path)
        self.db.init_schema()

    def ignore_item(self, item_id: int):
        self.db.toggle_ignore(item_id)
        print(f"Сущность {item_id} добавлена в игнор-лист.")


if __name__ == "__main__":
    app = TrackerAppCore()
    print("Ядро приложения инициализировано. База данных создана.")
