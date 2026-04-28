import HdRezkaApi
print(f"HdRezkaApi.search: {HdRezkaApi.search}")
import inspect
for name, obj in inspect.getmembers(HdRezkaApi.search):
    if not name.startswith('__'):
        print(f" - {name}")
