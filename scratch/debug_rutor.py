import re
try:
    with open('rutor_test.html', 'r', encoding='utf-8') as f:
        text = f.read()
except:
    with open('rutor_test.html', 'r', encoding='utf-16') as f:
        text = f.read()

print("KP links:", re.findall(r'kinopoisk\.ru[^\s\"\'<>]*', text))
print("IMDb links:", re.findall(r'imdb\.com[^\s\"\'<>]*', text))
