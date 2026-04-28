from HdRezkaApi import HdRezkaSearch
search = HdRezkaSearch("Гриффины")
print("Attributes of HdRezkaSearch instance:")
import inspect
for name, obj in inspect.getmembers(search):
    if not name.startswith('__'):
        print(f" - {name}")
