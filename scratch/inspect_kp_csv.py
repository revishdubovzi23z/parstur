import codecs, csv, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

with codecs.open('kinopoiskocenki.csv', 'r', 'utf-16') as f:
    content = f.read()

lines = content.splitlines()
reader = csv.DictReader(lines, delimiter='\t')
for i, row in enumerate(reader):
    row = {k.strip('"').strip(): v.strip('"').strip() for k, v in row.items() if k}
    items = list(row.items())[:8]
    print(dict(items))
    if i >= 4:
        break
