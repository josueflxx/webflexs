import os
import sys
import json
import time
import subprocess
from pathlib import Path
from types import SimpleNamespace
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit, unquote

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'flexs_project.settings.test'

import django
django.setup()

from django.template import Context
from django.template.loader import get_template
from django.contrib.staticfiles import finders

context = {
    'active_theme': 'blueprint',
    'user': SimpleNamespace(is_authenticated=True, is_staff=False, username='Vista previa'),
    'all_diameter_options_json': json.dumps(['7/16', '1/2', '3/4']),
    'laminated_diameter_options_json': json.dumps(['1/2', '3/4']),
    'diameter_options': ['7/16', '1/2', '3/4'],
    'profile_options': ['CURVA', 'SEMICURVA', 'PLANA'],
    'form_values': {'clamp_type': 'trefilada', 'diameter': '1/2', 'profile_type': 'CURVA', 'width_mm': 120, 'length_mm': 180},
    'site_settings': SimpleNamespace(company_email='ventas@flexs.com.ar', company_phone='+54 011 5177-9690')
}

rendered_home = get_template('core/home.html').template.render(Context(context)).encode('utf-8')

class PreviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(rendered_home)
            return
        if path == '/pedidos/carrito/count/':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"count":0}')
            return
        super().do_GET()

    def translate_path(self, path):
        name = unquote(urlsplit(path).path).removeprefix('/static/')
        if '..' in Path(name).parts:
            return str(PROJECT / 'missing')
        found = finders.find(name)
        if found:
            return found
        direct = PROJECT / 'core' / 'static' / name
        if direct.exists():
            return str(direct)
        return str(PROJECT / 'missing')

    def log_message(self, format, *args):
        pass  # Quiet logs

def run():
    port = 8792
    server = ThreadingHTTPServer(('127.0.0.1', port), PreviewHandler)
    import threading
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"Preview server running on http://127.0.0.1:{port}")

    node_script = f"""
import {{createRequire}} from 'node:module';
const require = createRequire('C:/Users/Brian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const {{chromium}} = require('playwright');

const outDir = 'C:/Users/Brian/.gemini/antigravity/brain/af73e395-e41b-4133-941e-81f75a94b553';

const browser = await chromium.launch({{
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true,
    args: ['--enable-unsafe-swiftshader']
}});

try {{
    const context = await browser.newContext({{
        viewport: {{width: 1440, height: 950}},
        deviceScaleFactor: 2
    }});
    const page = await context.newPage();
    await page.goto('http://127.0.0.1:{port}/', {{waitUntil: 'domcontentloaded'}});

    // Wait for button "Ver medidas"
    const btn = page.getByRole('button', {{name: 'Ver medidas', exact: true}});
    await btn.waitFor({{state: 'visible', timeout: 30000}});
    await btn.click();
    await page.waitForTimeout(1500);

    // --- Light Theme ---
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
    await page.waitForTimeout(600);

    const card = page.locator('#homeClamp');
    await card.screenshot({{path: `${{outDir}}/clamp_medidas_light_card.png`}});
    await page.screenshot({{path: `${{outDir}}/clamp_medidas_light.png`}});
    console.log('Light theme screenshots saved!');

    // --- Dark Theme ---
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
    await page.waitForTimeout(600);

    await card.screenshot({{path: `${{outDir}}/clamp_medidas_dark_card.png`}});
    await page.screenshot({{path: `${{outDir}}/clamp_medidas_dark.png`}});
    console.log('Dark theme screenshots saved!');

}} finally {{
    await browser.close();
}}
"""

    temp_script = PROJECT / 'scratch_capture.mjs'
    temp_script.write_text(node_script, encoding='utf-8')

    try:
        res = subprocess.run(['node', str(temp_script)], capture_output=True, text=True, timeout=60)
        print("STDOUT:", res.stdout)
        print("STDERR:", res.stderr)
    finally:
        if temp_script.exists():
            temp_script.unlink()
        server.shutdown()

if __name__ == '__main__':
    run()
