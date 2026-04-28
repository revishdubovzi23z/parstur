from HdRezkaApi import HdRezkaSearch
s = HdRezkaSearch("https://rezka.ag")
res = s.fast_search("Гриффины")
print(f"Results: {res}")
