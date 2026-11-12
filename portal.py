import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Portal(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.rows = json.loads(Path('fixtures/exhibitors.json').read_text())
        self.revision = 1
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path in ('/seed', '/advance'):
            with self.server.lock:
                if path == '/advance' and self.server.revision == 1:
                    self.server.rows.insert(2, {'reference': 'EXH-NEW', 'name': '合成插入展商', 'country': '合成市场甲', 'booth': 'B-01', 'description': '运行中插入的虚构展商'})
                    self.server.revision += 1
                body = json.dumps({'revision': self.server.revision, 'rows': self.server.rows}, ensure_ascii=False).encode()
            content_type = 'application/json; charset=utf-8'
        elif path in ('/', '/portal.js'):
            name = 'portal.html' if path == '/' else 'portal.js'
            body = (Path('web') / name).read_bytes()
            content_type = 'text/html; charset=utf-8' if path == '/' else 'text/javascript; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def serve():
    server = Portal()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    with serve() as base:
        print(base, flush=True)
        threading.Event().wait()
