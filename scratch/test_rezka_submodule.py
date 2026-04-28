from HdRezkaApi.search import search
print(f"search type: {type(search)}")
res = search("Гриффины")
print(f"Results: {res}")
for r in res:
    print(f" - {r.name}: {r.url}")
