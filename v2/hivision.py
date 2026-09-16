"""One per application: all cohort tasks and previews share endpoint budgets."""
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit


class HivisionRequests:
    def __init__(self, limit=1):
        self.limit = limit
        self.condition = threading.Condition()
        self.active = {}
        self.local = threading.local()

    @staticmethod
    def key(url):
        p = urlsplit(url.strip())
        path = p.path.rstrip('/')
        if path.endswith('/idphoto'): path = path[:-8]
        host = (p.hostname or '').lower()
        if ':' in host: host = '['+host+']'
        port = p.port
        if port and port != (443 if p.scheme == 'https' else 80): host += ':'+str(port)
        return urlunsplit((p.scheme.lower(), host, path, '', ''))

    def configure(self, limit):
        if type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError('Hivision 请求并发数必须为 1–16 的整数')
        with self.condition:
            self.limit = limit
            self.condition.notify_all()

    def acquire(self, key, block=False):
        with self.condition:
            while self.active.get(key, 0) >= self.limit:
                if not block: return False
                self.condition.wait(.2)
            self.active[key] = self.active.get(key, 0)+1
            return True

    def release(self, key):
        with self.condition:
            self.active[key] -= 1
            self.condition.notify_all()

    @contextmanager
    def slot(self, url, reserved=False):
        key = self.key(url)
        if getattr(self.local, 'key', None) == key:
            yield
            return
        if not reserved: self.acquire(key, block=True)
        self.local.key = key
        try: yield
        finally:
            self.local.key = None
            self.release(key)
