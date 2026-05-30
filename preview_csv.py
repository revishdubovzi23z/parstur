import csv


def print_csv_preview(filename, encoding="utf-8"):
    print(f"--- {filename} ---")
    try:
        with open(filename, encoding=encoding) as f:
            reader = csv.reader(f)
            headers = next(reader)
            row = next(reader)
            print("Headers:", headers)
            print("Row 1:", row)
    except Exception as e:
        print(f"Error reading {filename} with {encoding}: {e}")


print_csv_preview("backup_1379576_votes.csv", "utf-16le")
print_csv_preview("IMDBOCENKI.csv", "utf-8")
