from HdRezkaApi import HdRezkaSearch
search = HdRezkaSearch("Гриффины")
res = search.fast_search()
print(f"Result type: {type(res)}")
print(f"Result content: {res}")
