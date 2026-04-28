import HdRezkaApi
import inspect

print("Members of HdRezkaApi module:")
for name, obj in inspect.getmembers(HdRezkaApi):
    print(f" - {name}")
