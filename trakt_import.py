import csv
import re
import time
from datetime import datetime

from logging_config import setup_logging
from settings import settings
from tmdb_client import TMDBClient
from trakt_client import TraktClient

logger = setup_logging("trakt_import", settings.log_file_path)


def _norm(s):
    if not s:
        return ""
    return re.sub(r"[^a-zа-яё0-9]", "", s.lower())


def get_existing_trakt_data(client):
    logger.info("Получаем список уже просмотренных/оцененных фильмов и сериалов из Trakt.tv...")
    existing_imdb = set()
    existing_tmdb = set()
    existing_titles = set()

    def process_item(item_type, item_data):
        ids = item_data.get("ids", {})
        if ids.get("imdb"):
            existing_imdb.add(ids["imdb"])
        if ids.get("tmdb"):
            existing_tmdb.add(int(ids["tmdb"]))

        title = item_data.get("title")
        year = item_data.get("year")
        if title and year:
            existing_titles.add((_norm(title), int(year)))

    # 1. Fetch ratings (with expire_after=0 to bypass requests_cache local cache)
    url_ratings = f"{client.base_url}/sync/ratings"
    try:
        resp = client.session.get(url_ratings, headers=client.headers, timeout=15, expire_after=0)
        if resp.status_code == 200:
            items = resp.json()
            for item in items:
                m_type = item.get("type")
                if m_type in ("movie", "show"):
                    process_item(m_type, item.get(m_type, {}))
            logger.info(f"Получено {len(items)} оценок из Trakt.")
        else:
            logger.warning(f"Не удалось получить оценки: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"Ошибка при получении оценок: {e}")

    # 2. Fetch watched movies
    url_watched_movies = f"{client.base_url}/sync/watched/movies"
    try:
        resp = client.session.get(
            url_watched_movies, headers=client.headers, timeout=15, expire_after=0
        )
        if resp.status_code == 200:
            movies = resp.json()
            for m in movies:
                process_item("movie", m.get("movie", {}))
            logger.info(f"Получено {len(movies)} просмотренных фильмов.")
    except Exception as e:
        logger.error(f"Ошибка при получении просмотренных фильмов: {e}")

    # 3. Fetch watched shows
    url_watched_shows = f"{client.base_url}/sync/watched/shows"
    try:
        resp = client.session.get(
            url_watched_shows, headers=client.headers, timeout=15, expire_after=0
        )
        if resp.status_code == 200:
            shows = resp.json()
            for s in shows:
                process_item("show", s.get("show", {}))
            logger.info(f"Получено {len(shows)} просмотренных сериалов.")
    except Exception as e:
        logger.error(f"Ошибка при получении просмотренных сериалов: {e}")

    logger.info(
        f"Итого в Trakt: {len(existing_imdb)} уникальных IMDb ID, "
        f"{len(existing_tmdb)} TMDB ID, {len(existing_titles)} названий."
    )
    return existing_imdb, existing_tmdb, existing_titles


def authenticate_trakt(client):
    if client.access_token:
        return True

    print("\n--- АВТОРИЗАЦИЯ TRAKT.TV ---")
    print("1. Перейдите по этой ссылке (с включенным VPN/Прокси!):")
    print(client.get_auth_url())
    print("2. Нажмите 'YES' (разрешить).")
    print("3. Скопируйте полученный PIN-код.")

    pin = input("Введите PIN-код: ").strip()
    if not pin:
        print("Отменено.")
        return False

    if client.exchange_code(pin):
        print("Успешная авторизация!")
        return True
    else:
        print("Ошибка авторизации. Проверьте ключи и PIN-код.")
        return False


def push_batches(client, payload, batch_size=100, show_batch_size=10):
    movies = payload.get("movies", [])
    shows = payload.get("shows", [])

    logger.info(f"Начинаем отправку в Trakt: {len(movies)} фильмов, {len(shows)} сериалов...")

    def chunker(seq, size):
        return (seq[pos : pos + size] for pos in range(0, len(seq), size))

    def run_with_retry(func, **kwargs):
        for attempt in range(3):
            try:
                res = func(**kwargs)
                if res is not None:
                    return res
                logger.warning(
                    f"Попытка {attempt + 1} для {func.__name__} завершилась неудачей (сервер вернул ошибку). Повтор через 5 сек..."
                )
            except Exception as e:
                logger.warning(
                    f"Ошибка в {func.__name__} при попытке {attempt + 1}: {e}. Повтор через 5 сек..."
                )
            time.sleep(5)
        logger.error(f"Не удалось выполнить {func.__name__} после 3 попыток.")
        return None

    for chunk in chunker(movies, batch_size):
        logger.info(f"Отправляем батч фильмов ({len(chunk)} шт.)...")
        run_with_retry(client.sync_history, movies=chunk)
        time.sleep(1)
        run_with_retry(client.sync_ratings, movies=chunk)
        time.sleep(1)

    for chunk in chunker(shows, show_batch_size):
        logger.info(f"Отправляем батч сериалов ({len(chunk)} шт.)...")
        run_with_retry(client.sync_history, shows=chunk)
        time.sleep(2)
        run_with_retry(client.sync_ratings, shows=chunk)
        time.sleep(2)

    logger.info("Отправка батчей завершена!")


def import_imdb(client, existing_data, filename="IMDBOCENKI.csv"):
    logger.info(f"Начинаем импорт из IMDb ({filename})")
    payload = {"movies": [], "shows": []}
    existing_imdb, _, _ = existing_data
    skipped_count = 0

    try:
        with open(filename, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                imdb_id = row.get("Const")
                rating = row.get("Your Rating")
                date_rated = row.get("Date Rated")
                title_type = row.get("Title Type", "").lower()

                if not imdb_id or not rating:
                    continue

                if imdb_id in existing_imdb:
                    skipped_count += 1
                    continue

                item = {
                    "rating": int(rating),
                    "rated_at": f"{date_rated}T12:00:00.000Z"
                    if date_rated
                    else datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "watched_at": f"{date_rated}T12:00:00.000Z"
                    if date_rated
                    else datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "ids": {"imdb": imdb_id},
                }

                if "series" in title_type or "tv" in title_type:
                    payload["shows"].append(item)
                else:
                    payload["movies"].append(item)

        logger.info(f"Пропущено дубликатов из IMDb: {skipped_count}")
        push_batches(client, payload)

    except FileNotFoundError:
        logger.error(f"Файл {filename} не найден.")
    except Exception as e:
        logger.error(f"Ошибка парсинга {filename}: {e}", exc_info=True)


from concurrent.futures import ThreadPoolExecutor, as_completed


def import_kinopoisk(client, existing_data, filename="backup_1379576_votes.csv"):
    logger.info(f"Начинаем импорт из Кинопоиска ({filename})")
    tmdb = TMDBClient()
    payload = {"movies": [], "shows": []}
    existing_imdb, existing_tmdb, existing_titles = existing_data
    pre_skipped_count = 0
    post_skipped_count = 0

    try:
        with open(filename, encoding="utf-16le") as f:
            content = f.read()
            if content.startswith("\ufeff"):
                content = content[1:]

        import io

        reader = csv.DictReader(io.StringIO(content), delimiter="\t")

        rows = list(reader)
        total_rows = len(rows)
        logger.info(f"Всего записей в файле Кинопоиска: {total_rows}")

        # Prepare rows to search
        search_tasks = []
        for idx, row in enumerate(rows):
            rating = row.get("My rating")
            date_rated = row.get("Date")  # Format: dd.mm.yyyy, hh:mm
            title = row.get("Title")
            orig_title = row.get("Original Title")
            year_str = row.get("Year")

            if not rating or not title:
                continue

            try:
                year = int(year_str) if year_str and year_str.isdigit() else None
            except Exception:
                year = None

            # Pre-filter by Title + Year
            if year:
                title_norm = _norm(title)
                orig_norm = _norm(orig_title) if orig_title else ""
                if (title_norm, year) in existing_titles or (orig_norm, year) in existing_titles:
                    pre_skipped_count += 1
                    continue

            formatted_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
            if date_rated:
                try:
                    dt = datetime.strptime(date_rated, "%d.%m.%Y, %H:%M")
                    formatted_date = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                except Exception:
                    pass

            search_tasks.append(
                {
                    "title": title,
                    "year": year,
                    "orig_title": orig_title,
                    "rating": int(rating),
                    "formatted_date": formatted_date,
                    "row_num": idx + 1,
                }
            )

        logger.info(
            f"Отфильтровано дубликатов до поиска: {pre_skipped_count}. "
            f"Направлено на TMDB-поиск: {len(search_tasks)} записей."
        )

        processed_count = 0
        found_count = 0

        def process_single(task):
            title = task["title"]
            year = task["year"]
            orig_title = task["orig_title"]

            meta = tmdb.search_movie(title, year, alt_title=orig_title)
            if not meta or not meta.get("tmdb_id"):
                return None

            tmdb_id = int(meta["tmdb_id"])
            imdb_id = meta.get("imdb_id")

            if tmdb_id in existing_tmdb or (imdb_id and imdb_id in existing_imdb):
                return {"skipped": True, "title": title, "year": year}

            item = {
                "rating": task["rating"],
                "rated_at": task["formatted_date"],
                "watched_at": task["formatted_date"],
                "ids": {"tmdb": tmdb_id},
            }
            if imdb_id:
                item["ids"]["imdb"] = imdb_id

            return {
                "media_type": meta.get("media_type", "movie"),
                "item": item,
                "title": title,
                "year": year,
            }

        # 10 workers balances speed and rate limits perfectly
        max_workers = 10
        logger.info(f"Запускаем поиск в {max_workers} потоков...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_single, t): t for t in search_tasks}

            for future in as_completed(futures):
                task = futures[future]
                processed_count += 1
                try:
                    res = future.result()
                    if res:
                        if res.get("skipped"):
                            post_skipped_count += 1
                            continue
                        found_count += 1
                        if res["media_type"] == "tv":
                            payload["shows"].append(res["item"])
                        else:
                            payload["movies"].append(res["item"])
                    else:
                        logger.warning(f"Не найден на TMDB: {task['title']} ({task['year']})")
                except Exception as exc:
                    logger.error(
                        f"Ошибка поиска для {task['title']} ({task['year']}): {exc}", exc_info=True
                    )

                if processed_count % 50 == 0 or processed_count == len(search_tasks):
                    logger.info(
                        f"Прогресс поиска: {processed_count}/{len(search_tasks)} (Найдено новых: {found_count}, Пропущено после поиска: {post_skipped_count})"
                    )

        logger.info(
            f"Поиск завершен. Найдено новых на TMDB: {found_count} из {len(search_tasks)}. "
            f"Пропущено дубликатов до поиска: {pre_skipped_count}, "
            f"пропущено дубликатов после поиска: {post_skipped_count}."
        )

        if payload["movies"] or payload["shows"]:
            push_batches(client, payload)
        else:
            logger.info("Нет данных для отправки в Trakt.")

    except FileNotFoundError:
        logger.error(f"Файл {filename} не найден.")
    except Exception as e:
        logger.error(f"Ошибка парсинга {filename}: {e}", exc_info=True)


if __name__ == "__main__":
    if not settings.trakt_client_id:
        print("ОШИБКА: Заполните TRAKT_CLIENT_ID и TRAKT_CLIENT_SECRET в файле .env")
        exit(1)

    client = TraktClient()
    if not authenticate_trakt(client):
        exit(1)

    existing_data = get_existing_trakt_data(client)

    print("\nЧто импортируем?")
    print("1. IMDb (IMDBOCENKI.csv)")
    print("2. Kinopoisk (backup_1379576_votes.csv)")
    print("3. Оба")
    choice = input("Выберите (1/2/3): ").strip()

    if choice in ("1", "3"):
        import_imdb(client, existing_data)
    if choice in ("2", "3"):
        import_kinopoisk(client, existing_data)

    print("\nИмпорт завершен!")
