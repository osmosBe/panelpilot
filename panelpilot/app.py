"""Same-origin authenticated management API and offline-capable static UI."""
import hmac
import asyncio
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from . import __version__
from .auth import Auth, SESSION_SECONDS, make_credentials
from .controller import Controller, DemoController, Conflict, ApplyError
from .models import LoginRequest, PasswordRequest, SaveRequest
from .storage import write_json
from .admin_client import AdminClient, DemoAdmin, AdminUnavailable
from .system_models import Maintenance, NFS
from .photos import PhotoStore, MAX_UPLOAD

COOKIE = 'panelpilot_session'
STATIC = Path(__file__).parent/'static'


def create_app(controller=None, auth=None, demo=None, admin=None, photos=None):
    demo = os.environ.get('PANELPILOT_DEMO') == '1' if demo is None else demo
    state = Path(os.environ.get('PANELPILOT_STATE', str(Path.home()/'.local/state/panelpilot')))
    if controller is None:
        if demo:
            state.mkdir(parents=True, exist_ok=True)
            controller = DemoController(state/'settings.json')
            if not (state/'auth.json').exists():
                write_json(state/'auth.json', make_credentials('demo-panelpilot', 'demo'))
        else:
            runtime = json.loads(Path(os.environ['PANELPILOT_RUNTIME']).read_text())
            controller = Controller(os.environ['PANELPILOT_SETTINGS'], state, runtime)
    if auth is not None: state=auth.path.parent
    auth = auth or Auth(state/'auth.json')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.controller, app.state.auth = controller, auth
    admin=admin or (DemoAdmin(state/'system-demo.json') if demo else AdminClient())
    photos=photos or PhotoStore(getattr(controller,'runtime',{}).get('upload_directory',str(state/'photos')))
    upload_lock=asyncio.Lock()
    allowed_hosts = getattr(controller, 'runtime', {}).get('allowed_hosts', ['localhost', '127.0.0.1', '::1', 'testserver'])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    networks = [ipaddress.ip_network(value) for value in ('127.0.0.0/8', '::1/128', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7', 'fe80::/10')]

    @app.middleware('http')
    async def boundaries(request, call_next):
        try:
            peer = ipaddress.ip_address(request.client.host)
            if not any(peer in network for network in networks):
                return JSONResponse({'detail': 'Nur im lokalen Netzwerk verfügbar.'}, status_code=403)
        except ValueError:
            return JSONResponse({'detail': 'Ungültige Clientadresse.'}, status_code=403)
        # Credentials and command arguments never appear in access logs or URLs.
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            upload=request.method=='POST' and request.url.path=='/api/photos/upload'
            if upload:
                try: session(request,True)
                except HTTPException as exc: return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
            if not upload and request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'detail': 'JSON erforderlich.'}, status_code=415)
            try:
                if int(request.headers.get('content-length', '0')) > (MAX_UPLOAD if upload else 16384):
                    return JSONResponse({'detail': 'Anfrage zu groß.'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': 'Ungültige Anfrage.'}, status_code=400)
            # Check the actual body too, including chunked requests.
            if not upload:
                total, chunks = 0, []
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > 16384:
                        return JSONResponse({'detail': 'Anfrage zu groß.'}, status_code=413)
                    chunks.append(chunk)
                request._body = b''.join(chunks)
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': 'Fremder Ursprung nicht erlaubt.'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'Fremder Ursprung nicht erlaubt.'}, status_code=403)
        response = await call_next(request)
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
        })
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default errors include submitted values (possibly passwords).
        messages = [{'field': '.'.join(map(str, e['loc'][1:])), 'message': e['msg']} for e in exc.errors()]
        return JSONResponse({'detail': 'Bitte die Eingaben prüfen.', 'fields': messages}, status_code=422)

    def session(request, write=False):
        value = auth.session(request.cookies.get(COOKIE))
        if not value:
            raise HTTPException(401, 'Bitte anmelden.')
        if write and not hmac.compare_digest(request.headers.get('x-csrf-token', ''), value['csrf']):
            raise HTTPException(403, 'Sitzung abgelaufen. Bitte neu anmelden.')
        return value

    @app.get('/')
    def index():
        return FileResponse(STATIC/'index.html')

    @app.post('/api/login')
    def login(body: LoginRequest, request: Request):
        if request.url.scheme != 'https' and not demo and request.client.host not in ('127.0.0.1', '::1'):
            raise HTTPException(400, 'Für den Netzwerkzugriff ist HTTPS erforderlich.')
        try:
            result = auth.login(body.username, body.password, request.client.host)
        except ValueError:
            raise HTTPException(429, 'Zu viele Versuche. Bitte eine Minute warten.') from None
        if not result:
            raise HTTPException(401, 'Benutzername oder Passwort falsch.')
        token, value = result
        response = JSONResponse({'username': value['username'], 'csrf': value['csrf'], 'demo': demo, 'version': __version__})
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=request.url.scheme == 'https', samesite='strict', path='/')
        return response

    @app.get('/api/session')
    def current_session(request: Request):
        value = session(request)
        return {'username': value['username'], 'csrf': value['csrf'], 'demo': demo, 'version': __version__}

    @app.post('/api/logout')
    def logout(request: Request):
        session(request, True)
        auth.logout(request.cookies.get(COOKIE))
        response = JSONResponse({'success': True})
        response.delete_cookie(COOKIE, path='/', secure=request.url.scheme == 'https', httponly=True, samesite='strict')
        return response

    @app.get('/api/settings')
    def settings(request: Request):
        session(request)
        return controller.read()

    @app.put('/api/settings')
    def save(body: SaveRequest, request: Request):
        session(request, True)
        try:
            return controller.save(body.settings, body.revision)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ApplyError as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get('/api/status')
    def status(request: Request):
        session(request)
        try:
            return controller.status()
        except Exception:
            raise HTTPException(503, 'Die Panelsitzung ist momentan nicht erreichbar.') from None

    @app.post('/api/photos/check')
    def check_photos(request: Request):
        session(request, True)
        return controller.photos()

    @app.exception_handler(AdminUnavailable)
    async def admin_error(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=503)

    @app.get('/api/system')
    def system_status(request:Request):
        session(request)
        return admin.call('status')

    @app.put('/api/system/maintenance')
    def maintenance(body:Maintenance,request:Request):
        session(request,True)
        return admin.call('maintenance.apply',body.model_dump())

    @app.put('/api/system/nfs')
    def nfs(body:NFS,request:Request):
        session(request,True)
        if body.enabled:
            result=admin.test_nfs(body.model_dump())
            if result.get('status') not in ('ready','empty'):
                raise HTTPException(422,'NFS ist erreichbar, aber Bilder oder Ordner sind nicht lesbar.')
        return admin.call('nfs.apply',body.model_dump())

    @app.post('/api/system/nfs/test')
    def nfs_test(body:NFS,request:Request):
        session(request,True)
        return admin.test_nfs(body.model_dump())

    @app.get('/api/photos/local')
    def local_photos(request:Request):
        session(request)
        return photos.listing()

    @app.get('/api/photos/local/{name}')
    def local_photo(name:str,request:Request):
        session(request)
        try: return FileResponse(photos.file(name))
        except (ValueError,FileNotFoundError): raise HTTPException(404,'Foto nicht gefunden.') from None

    @app.delete('/api/photos/local/{name}')
    def delete_photo(name:str,request:Request):
        session(request,True)
        try: photos.delete(name)
        except (ValueError,FileNotFoundError): raise HTTPException(404,'Foto nicht gefunden.') from None
        return {'success':True}

    @app.post('/api/photos/upload')
    async def upload_photo(request:Request):
        session(request,True)
        if upload_lock.locked(): raise HTTPException(429,'Ein Upload läuft bereits. Bitte kurz warten.')
        async with upload_lock:
            handle,temporary=photos.temporary()
            try:
                total=0
                with handle:
                    async for chunk in request.stream():
                        total+=len(chunk)
                        if total>MAX_UPLOAD: raise HTTPException(413,'Ein Foto darf höchstens 20 MiB groß sein.')
                        handle.write(chunk)
                return await run_in_threadpool(photos.finish,temporary,unquote(request.headers.get('x-filename','Foto')))
            except ValueError as exc: raise HTTPException(422,str(exc)) from None
            except OSError: raise HTTPException(507,'Das Foto konnte nicht gespeichert werden. Freien Speicher und Dateirechte prüfen.') from None
            finally: temporary.unlink(missing_ok=True)

    @app.post('/api/dashboard/reload')
    def reload_dashboard(request: Request):
        session(request, True)
        try:
            controller.reload_dashboard()
        except Exception:
            raise HTTPException(503, 'Das Dashboard konnte nicht neu geladen werden.') from None
        return {'success': True}

    @app.post('/api/password')
    def password(body: PasswordRequest, request: Request):
        session(request, True)
        if demo:
            raise HTTPException(400, 'Das Demo-Passwort ist fest vorgegeben.')
        if not auth.change_password(body.current_password, body.new_password):
            raise HTTPException(400, 'Das bisherige Passwort stimmt nicht.')
        response = JSONResponse({'success': True})
        response.delete_cookie(COOKIE, path='/')
        return response

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app
