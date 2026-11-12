"""Run from any directory: python web/server.py."""

import argparse
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from lunch_web.app import Application


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description='今天吃什么 · 网页多人版')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8000, type=int)
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent / 'data')
    parser.add_argument('--secure-cookie', action='store_true', help='仅在 HTTPS 反向代理后使用')
    parser.add_argument('--production', action='store_true', help='使用 Waitress 长期运行')
    args = parser.parse_args()
    app = Application(args.data_dir / 'web.sqlite3', secure_cookie=args.secure_cookie)
    print(f'今天吃什么: http://{args.host}:{args.port}', flush=True)
    print(f'数据库: {app.database.path.resolve()}', flush=True)
    if args.production:
        try:
            from waitress import serve
        except ImportError:
            parser.error('请先安装 web/requirements.txt 中的 Waitress。')
        serve(app, host=args.host, port=args.port, threads=8, max_request_body_size=65536)
    else:
        print('本地开发模式；长期运行请安装依赖并添加 --production。', flush=True)
        with make_server(args.host, args.port, app, server_class=ThreadedServer) as server:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == '__main__':
    main()
