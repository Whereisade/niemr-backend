import threading
_local = threading.local()

def set_request(req): _local.request = req
def get_request(): return getattr(_local, "request", None)
def clear_request():
    if hasattr(_local, "request"):
        delattr(_local, "request")
