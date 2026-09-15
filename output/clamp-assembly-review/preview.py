"""Serve the review variants locally, without changing the deployed homepage."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, unquote

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
STATIC = PROJECT / 'core/static'


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/', '/actual', '/propuesta'):
            draft = path != '/actual'
            html = (HERE / 'home.html').read_text(encoding='utf-8')
            if draft:
                html = html.replace('data-model="/static/core/models/abrazadera-curva.glb"',
                                    'data-model="/preview/abrazadera-plaqueta-tuercas.glb"')
            navigation = '''<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px;font-size:13px;align-items:center">
                <span style="color:#ffad8d;width:100%;margin-bottom:4px">Comparación local · propuesta sin publicar</span>
                <a href="/actual" style="color:#ddd;border:1px solid #ffffff30;border-radius:8px;padding:10px 12px">Modelo publicado</a>
                <a href="/propuesta" style="color:#ddd;border:1px solid #ff6b3570;border-radius:8px;padding:10px 12px">Con plaqueta y tuercas</a>
                </div>'''
            html = html.replace('<div class="home-clamp-heading">', navigation+'<div class="home-clamp-heading">',1)
            if draft:
                html = html.replace('Abrazaderas · Fabricación FLEXS</span>', 'Abrazadera con plaqueta y tuercas</span>')
            data = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/pedidos/carrito/count/':
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.end_headers()
            self.wfile.write(b'{"count":0}')
            return
        super().do_GET()

    def translate_path(self,path):
        path = unquote(urlsplit(path).path)
        if path == '/preview/abrazadera-plaqueta-tuercas.glb':
            return str(HERE / 'abrazadera-plaqueta-tuercas.glb')
        if path.startswith('/static/'):
            candidate = (STATIC / path.removeprefix('/static/')).resolve()
            if candidate.is_relative_to(STATIC.resolve()):
                return str(candidate)
        return str(HERE / 'not-found')


if __name__ == '__main__':
    print('LOCAL REVIEW http://127.0.0.1:8774/propuesta',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8774),Handler).serve_forever()
