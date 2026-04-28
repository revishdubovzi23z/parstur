import re
def check(f):
    try:
        with open(f, 'r', encoding='utf-8') as f_in:
            text = f_in.read()
    except:
        with open(f, 'r', encoding='latin-1') as f_in:
            text = f_in.read()
    print(f"File {f}:")
    print("  KP:", re.findall(r'kinopoisk\.ru/rating/(\d+)\.gif', text))
    print("  IMDb:", re.findall(r'imdb\.com/title/(tt\d+)', text))

check('rutor_check_1.html')
check('rutor_check_2.html')
