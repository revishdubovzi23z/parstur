from HdRezkaApi import HdRezkaSearch
s = HdRezkaSearch()
res = s.advanced_search("Гриффины")
print(f"Result type: {type(res)}")
for r in res:
    print(f" - {r.name}: {r.url}")
