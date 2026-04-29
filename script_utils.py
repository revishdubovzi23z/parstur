import json
import os

_config = None


def load_config():
    global _config
    if _config is None:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.json"
        )
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                _config = json.load(f)
        else:
            _config = {}
    return _config


def should_stop(status_key):
    return os.path.exists(f"stop_{status_key}.flag")


def save_checkpoint(status_key, data):
    with open(f"checkpoint_{status_key}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_checkpoint(status_key):
    path = f"checkpoint_{status_key}.json"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return None
    return None


def clear_checkpoint(status_key):
    path = f"checkpoint_{status_key}.json"
    if os.path.exists(path):
        try:
            os.remove(path)
        except:
            pass


def clear_stop_flag(status_key):
    path = f"stop_{status_key}.flag"
    if os.path.exists(path):
        try:
            os.remove(path)
        except:
            pass
