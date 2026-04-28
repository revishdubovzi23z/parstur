from HdRezkaApi import HdRezkaApi
import inspect

print("Attributes of HdRezkaApi:")
for name, obj in inspect.getmembers(HdRezkaApi):
    print(f" - {name}")
