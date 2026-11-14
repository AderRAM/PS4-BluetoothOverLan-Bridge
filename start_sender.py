import paramiko, re, time, sys, os, getpass

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    key_file = os.path.expanduser('~/.ssh/id_rsa')
    password = os.environ.get('DS4_PASSWORD')
    if os.path.exists(key_file):
        c.connect('192.168.1.100', username='prizmo', key_filename=key_file, timeout=10)
    elif password:
        c.connect('192.168.1.100', username='prizmo', password=password, timeout=10)
    else:
        password = getpass.getpass('SSH password for prizmo@192.168.1.100: ')
        c.connect('192.168.1.100', username='prizmo', password=password, timeout=10)
except Exception as e:
    print(f'SSH failed: {e}')
    sys.exit(1)

print('Connecting DS4 via Bluetooth...')
_, out, _ = c.exec_command('bluetoothctl -- connect AC:36:1B:8F:11:3D 2>&1', timeout=10)
result = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', out.read().decode('utf-8', errors='replace')).strip()
print(result)

if 'successful' not in result.lower() and 'already' not in result.lower():
    print('Bluetooth connect failed.')
    c.close()
    sys.exit(1)

_, _o, _e = c.exec_command('pkill -f sender.py 2>/dev/null')
_o.read(); _e.read()
time.sleep(1)

chan = c.get_transport().open_session()
chan.exec_command('bash -c "nohup python3 -u /home/prizmo/ds4bridge/sender.py > /home/prizmo/ds4bridge/sender.log 2>&1 &"')
time.sleep(2)

_, out, _ = c.exec_command('cat ~/ds4bridge/sender.log')
log = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', out.read().decode('utf-8', errors='replace')).strip()
print(log)
c.close()

if 'Sending to' not in log:
    print('Sender did not start correctly.')
    sys.exit(1)

print('Sender active.')
