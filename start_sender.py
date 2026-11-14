#!/usr/bin/env python3
"""Inicia o sender.py em uma maquina Linux remota via SSH.

Cada valor e resolvido nesta ordem:
  argumento  >  variavel de ambiente  >  ~/.config/ds4bridge/sender.json  >  pergunta (e salva)

Senhas nunca sao aceitas por argumento (ficariam no historico do shell).
Autenticacao: chaves SSH / ssh-agent primeiro; senha so se isso falhar.
"""
import argparse, getpass, json, os, re, shlex, sys, time
from pathlib import Path

try:
    import paramiko
except ImportError:
    sys.exit('Falta o paramiko: pip install paramiko')

CONFIG_DIR  = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'ds4bridge'
CONFIG_FILE = CONFIG_DIR / 'sender.json'
MAC_RE      = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')
ANSI_RE     = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    except OSError as err:
        print(f'Aviso: nao foi possivel salvar {CONFIG_FILE}: {err}')


def ask(label, value, validate=None):
    """Devolve value se ja existir; senao pergunta ao usuario (so em terminal interativo)."""
    while True:
        if not value:
            if not sys.stdin.isatty():
                sys.exit(f'Falta {label}. Passe por argumento ou grave em {CONFIG_FILE}.')
            value = input(f'{label}: ').strip()
            continue
        if validate and not validate(value):
            if not sys.stdin.isatty():
                sys.exit(f'Valor invalido para {label}: {value}')
            print('  valor invalido, tente de novo')
            value = None
            continue
        return value


def remote_path(path):
    """Deixa o shell remoto expandir ~ (aspas simples impediriam)."""
    if path == '~':
        return '"$HOME"'
    if path.startswith('~/'):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def run(client, cmd, timeout=15):
    _, out, _ = client.exec_command(cmd, timeout=timeout)
    out.channel.set_combine_stderr(True)
    return ANSI_RE.sub('', out.read().decode('utf-8', errors='replace')).strip()


def main():
    p = argparse.ArgumentParser(description='Inicia o sender do DS4 via SSH.')
    p.add_argument('--host',        default=os.environ.get('DS4_HOST'),
                   help='Host/IP da maquina do sender (env DS4_HOST)')
    p.add_argument('--user',        default=os.environ.get('DS4_USER'),
                   help='Usuario SSH (env DS4_USER)')
    p.add_argument('--key',         default=os.environ.get('DS4_KEY'),
                   help='Chave privada SSH (opcional; sem isso usa ssh-agent e ~/.ssh)')
    p.add_argument('--bt-mac',      default=os.environ.get('DS4_BT_MAC'),
                   help='MAC Bluetooth do controle (env DS4_BT_MAC)')
    p.add_argument('--remote-dir',  default=os.environ.get('DS4_REMOTE_DIR'),
                   help='Pasta do sender.py na maquina remota (padrao: ~/ds4bridge)')
    p.add_argument('--sender-args', default='',
                   help='Argumentos extras repassados ao sender.py, ex: "--port 5000"')
    args = p.parse_args()

    cfg = load_config()
    host       = ask('Host/IP do sender', args.host or cfg.get('host'))
    user       = ask('Usuario SSH',       args.user or cfg.get('user'))
    bt_mac     = ask('MAC Bluetooth do DS4 (AA:BB:CC:DD:EE:FF)',
                     args.bt_mac or cfg.get('bt_mac'), MAC_RE.match)
    remote_dir = args.remote_dir or cfg.get('remote_dir') or '~/ds4bridge'
    key        = args.key or cfg.get('key')

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    conn = dict(hostname=host, username=user, timeout=10)
    if key:
        conn['key_filename'] = os.path.expanduser(key)

    try:
        try:
            client.connect(**conn)
        except paramiko.AuthenticationException:
            password = os.environ.get('DS4_PASSWORD') or getpass.getpass(f'Senha SSH de {user}@{host}: ')
            client.connect(**conn, password=password, look_for_keys=False, allow_agent=False)
    except (paramiko.SSHException, OSError) as err:
        sys.exit(f'Falha no SSH: {err}')

    # conexao ok: lembra os valores para a proxima vez
    new_cfg = {'host': host, 'user': user, 'bt_mac': bt_mac, 'remote_dir': remote_dir}
    if key:
        new_cfg['key'] = key
    save_config(new_cfg)

    print('Conectando o DS4 via Bluetooth...')
    result = run(client, f'bluetoothctl -- connect {bt_mac} 2>&1', timeout=15)
    print(result)
    if 'successful' not in result.lower() and 'already' not in result.lower():
        client.close()
        sys.exit('Falha ao conectar o Bluetooth.')

    # [p] evita que o pkill case com a propria linha de comando
    run(client, "pkill -f '[p]ython3 -u sender.py' 2>/dev/null; true")
    time.sleep(1)

    rd    = remote_path(remote_dir)
    extra = ' '.join(shlex.quote(a) for a in shlex.split(args.sender_args))
    inner = f'cd {rd} && nohup python3 -u sender.py {extra} > sender.log 2>&1 < /dev/null &'
    _, out, _ = client.exec_command('bash -c ' + shlex.quote(inner))
    out.channel.recv_exit_status()
    time.sleep(2)

    log = run(client, f'cat {rd}/sender.log')
    print(log)
    client.close()

    if 'Sending to' not in log:
        sys.exit('O sender nao iniciou corretamente.')
    print('Sender ativo.')


if __name__ == '__main__':
    main()