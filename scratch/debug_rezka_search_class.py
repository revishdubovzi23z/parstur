from HdRezkaApi import HdRezkaSearch
print(f"HdRezkaSearch: {HdRezkaSearch}")
import inspect
for name, obj in inspect.getmembers(HdRezkaSearch):
    if not name.startswith('__'):
        print(f" - {name}")
