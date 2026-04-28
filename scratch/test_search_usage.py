from HdRezkaApi import HdRezkaSearch
search = HdRezkaSearch("Гриффины")
print(f"Search results type: {type(search.results)}")
for res in search.results:
    print(f"Result: {res.name} - {res.url}")
