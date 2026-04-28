import io
import sys

def check_log():
    try:
        with open('sync_rezka_log.txt', 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for i, line in enumerate(lines):
                if 'Отмороженные' in line:
                    print(f"Found on line {i+1}: {line.strip()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_log()
