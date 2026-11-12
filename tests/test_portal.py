import json
import socket
import subprocess
import sys
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen


class PortalEntrypointTests(unittest.TestCase):
    def test_explicit_host_and_port_serve_page_and_data(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        process = subprocess.Popen(
            [sys.executable, 'portal.py', '--host', '127.0.0.1', '--port', str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with urlopen(base, timeout=1) as response:
                        page = response.read().decode()
                    break
                except URLError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail('目录启动入口未在指定地址提供页面')
                    time.sleep(0.02)
            self.assertIn('<title>合成展商目录</title>', page)
            with urlopen(base + '/seed', timeout=1) as response:
                seed = json.load(response)
            self.assertEqual(seed['revision'], 1)
            self.assertEqual(len(seed['rows']), 17)
            with urlopen(base + '/portal.js', timeout=1) as response:
                self.assertIn("fetch(path)", response.read().decode())
        finally:
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(stdout.strip(), base)
        self.assertEqual(stderr, '')
