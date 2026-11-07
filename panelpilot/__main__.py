import argparse
import getpass
import json
import os
from pathlib import Path

from .auth import make_credentials
from .controller import Controller
from .storage import write_json


def main():
    parser = argparse.ArgumentParser(prog='panelpilot')
    sub = parser.add_subparsers(dest='action', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8443)
    serve.add_argument('--certificate')
    serve.add_argument('--key')
    serve.add_argument('--demo', action='store_true')
    sub.add_parser('apply')
    sub.add_parser('reset-password')
    args = parser.parse_args()
    state = Path(os.environ.get('PANELPILOT_STATE', str(Path.home()/'.local/state/panelpilot')))
    if args.action == 'reset-password':
        password = getpass.getpass('Neues Passwort (mindestens 14 Zeichen): ')
        if len(password) < 14 or len(password) > 128 or password != getpass.getpass('Passwort wiederholen: '):
            parser.error('Passwörter stimmen nicht überein oder Länge ungültig.')
        username = json.loads((state/'auth.json').read_text())['username'] if (state/'auth.json').exists() else 'admin'
        write_json(state/'auth.json', make_credentials(password, username))
        print('Passwort geändert. Vorhandene Websitzungen werden abgemeldet.')
    elif args.action == 'apply':
        runtime = json.loads(Path(os.environ['PANELPILOT_RUNTIME']).read_text())
        controller = Controller(os.environ['PANELPILOT_SETTINGS'], state, runtime)
        controller.reconcile()
        print('PanelPilot-Einstellungen übernommen.')
    else:
        if bool(args.certificate) != bool(args.key):
            parser.error('Zertifikat und Schlüssel müssen gemeinsam angegeben werden.')
        if args.host not in ('127.0.0.1', '::1', 'localhost') and not args.certificate:
            parser.error('Netzwerkzugriff benötigt HTTPS: --certificate und --key angeben.')
        if args.demo:
            if args.host not in ('127.0.0.1', '::1', 'localhost'):
                parser.error('Die Demo darf nur auf Loopback laufen.')
            os.environ['PANELPILOT_DEMO'] = '1'
            if 'PANELPILOT_STATE' not in os.environ:
                os.environ['PANELPILOT_STATE'] = str(Path.cwd()/'.demo-state')
        import uvicorn
        uvicorn.run('panelpilot.app:create_app', factory=True, host=args.host, port=args.port,
                    ssl_certfile=args.certificate, ssl_keyfile=args.key, workers=1,
                    proxy_headers=False, access_log=False, server_header=False,
                    limit_concurrency=32, timeout_keep_alive=5)


if __name__ == '__main__':
    main()
