"""Local HTTP warning for domains in the AI blocklist. Does not intercept TLS."""
from pathlib import Path
from starlette.responses import HTMLResponse

ROOT = Path(__file__).resolve().parents[1]
PAGE = '''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Использование нейросетей запрещено</title>
<style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#f5f5f2;color:#171717;font-family:Arial,sans-serif}main{max-width:680px;padding:36px;background:white;border:1px solid #ddd;border-radius:16px}h1{font-size:clamp(24px,5vw,36px);line-height:1.25}p{line-height:1.6}a{display:inline-block;background:#202020;color:white;text-decoration:none;padding:14px 20px;border-radius:8px}</style></head>
<body><main><h1>Использование нейросетей на время тестирования запрещено</h1>
<p>Вернитесь к тесту и отвечайте самостоятельно.</p>
<a href="http://test.int:8000/">Вернуться к тестированию</a></main></body></html>'''


def blocked_domains():
    paths = [ROOT / 'deploy/blocked-ai-domains.txt']
    local = Path('/etc/control-test/ai-warning-domains.txt')
    if local.exists():
        paths.append(local)
    return {line.strip().lower().rstrip('.') for path in paths
            for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#')}


def response():
    return HTMLResponse(PAGE, status_code=403, headers={'Cache-Control': 'no-store'})


class AIWarningMiddleware:
    def __init__(self, app):
        self.app = app
        self.domains = blocked_domains()

    async def __call__(self, scope, receive, send):
        host = next((v.decode('latin1') for k, v in scope.get('headers', []) if k == b'host'), '')
        host = host.split(':', 1)[0].lower().rstrip('.')
        if any(host == domain or host.endswith('.' + domain) for domain in self.domains):
            if scope['type'] == 'http':
                await response()(scope, receive, send)
                return
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 4403})
                return
        await self.app(scope, receive, send)
