# PS4-BluetoothOverLan-Bridge

Ponte que leva um controle **DualShock 4 (PS4)** conectado por Bluetooth a um PC pela **rede local (UDP)**. No PC, o controle pode funcionar como:

- **Gamepad virtual**
- **Mouse e teclado**, inclusive com **giroscópio**
- **Controle de apresentação** (LibreOffice Impress)

## Como funciona

```
DS4 ──Bluetooth──► Sender (máquina Linux) ──UDP──► Receiver (Windows ou Linux)
                                                        │
                                                        └─► gamepad / mouse / teclado virtual
```

- **Sender** (`sender.py`): roda em uma máquina Linux com o controle pareado por Bluetooth. Lê os eventos via `evdev` e envia o estado do controle a 20 Hz por UDP.
- **Receiver** (`receiver.py` para Windows, `receiver_linux.py` para Linux): escuta na porta `9999`, recebe os pacotes e os traduz em entradas virtuais.
- **Presenter** (`presenter.py`): controle de apresentação independente, **sem rede**.

## Requisitos

| Componente | Requisitos |
|---|---|
| Sender | Linux, Python 3, `evdev`, controle DS4 pareado por Bluetooth |
| Receiver (Windows) | Python 3, `vgamepad`, **executar como administrador** |
| Receiver (Linux) | Python 3, `evdev`, acesso de escrita a `/dev/uinput` |
| `start_sender.py` (opcional) | Python 3, `paramiko`, acesso SSH à máquina do sender |

Opcional no Linux: `xdotool` ou `ydotool` para centralizar o mouse.

### Linux: permissão para `/dev/uinput`

```bash
sudo usermod -aG input $USER
# saia da sessão e entre novamente
```

Ou, com uma regra udev:

```bash
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Windows: por que administrador?

O `SendInput` é bloqueado pelo UIPI em processos sem elevação quando a janela em foco está elevada. Abra o terminal como administrador antes de iniciar o receiver.

## Uso rápido

1. **No PC que vai receber** (onde o controle será usado):

   ```bash
   # Linux
   python3 receiver_linux.py

   # Windows (terminal como administrador)
   python receiver.py
   ```

2. **Na máquina Linux com o controle pareado**, inicie o sender:

   ```bash
   python3 sender.py
   ```

   Ou inicie-o remotamente por SSH a partir do seu PC:

   ```bash
   python3 start_sender.py
   ```

3. **Controle de apresentação** (sem rede):

   ```bash
   python3 presenter.py
   ```

### Opções do receiver Linux

| Opção | Efeito |
|---|---|
| `--no-hotkeys` | Pula a configuração de atalhos (key-learn) |
| `--reverse` | Inverte a direção do giroscópio no mouse (apontar para cima move o cursor para baixo) |

## Modo apresentação

Dois modos, alternados pelo botão **L3**:

- **Modo 0**: navegação de slides.
- **Modo 1**: mouse controlado pelo giroscópio.

## Atalhos (key-learn)

Na primeira execução, o receiver pede que você escolha três teclas para as funções abaixo:

| Função | O que faz |
|---|---|
| **G6** | Alterna giroscópio → analógico (a origem depende do G5) |
| **G5** | Liga/desliga o modo mouse (chave geral) |
| **G4** | Centraliza o cursor do mouse |

Como configurar:

1. Pressione a tecla escolhida para **G6** **duas vezes em até 2 segundos**.
2. Pressione uma **tecla diferente** uma vez: ela vira o **G5**.
3. Pressione outra **tecla diferente** uma vez: ela vira o **G4**.

Observações:

- Teclas de **F1 a F12** sempre funcionam. Letras, números e modificadores são bloqueados durante a configuração.
- Pressione **ESC** a qualquer momento para pular a configuração.
- No Linux, a configuração é salva em `~/.config/ds4bridge/hotkeys.conf` ao encerrar o receiver.
- Para refazer: apague esse arquivo e inicie o receiver novamente.

## Comportamentos úteis de saber

- **Timeout de pacotes**: se não chegarem pacotes por mais de 2,5 s, o receiver reinicia o controle virtual.
- **DS4 por USB**: se um DS4 físico for conectado por USB ao PC, a ponte **pausa** automaticamente para evitar entrada duplicada.
- **Bateria**: o nível é lido de `/sys/class/power_supply/ps-controller-battery-*/capacity` a cada 10 s. Se o caminho não existir, aparece `--`.
- **Zona morta do giroscópio**: valores abaixo de `1200` (unidade bruta) são tratados como zero, para ignorar o ruído em repouso.
- **L1/R1 no modo mouse**: deixam de ir para o gamepad e passam a acionar os botões do mouse.

## Estrutura do projeto

| Arquivo | Função |
|---|---|
| `sender.py` | Lê o DS4 (evdev) e transmite por UDP |
| `start_sender.py` | Inicia o sender remotamente por SSH (paramiko) |
| `receiver.py` | Receiver para Windows (vgamepad + SendInput) |
| `receiver_linux.py` | Receiver para Linux (evdev/UInput) |
| `presenter.py` | Controle de apresentação independente |
| `ds4presenter_26-06-22/` | Implementação alternativa do presenter (interface e lógica separadas) |