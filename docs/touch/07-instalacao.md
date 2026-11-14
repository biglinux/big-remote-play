# 07 — Instalação nesta máquina

## Estado antes

| Pacote | Versão instalada |
|---|---|
| `sunshine` | 2026.914.233613-3 |
| `big-remote-play` | 26.10.07-1217 |
| `big-keyboard` | 0.1.0-1 |
| `wlmaker` | 1:0.8.r80.g7481afe-36 |

`sudo` pede senha nesta máquina, então a instalação dos pacotes não pôde
ser feita automaticamente nesta sessão. Os pacotes foram **gerados e
testados**; falta só o comando com a sua senha.

## Pacotes gerados

Copiados para `~/Documentos/Git/brp-touch-pacotes/`:

| Arquivo | Origem |
|---|---|
| `big-keyboard-0.1.0-2-x86_64.pkg.tar` | `big-keyboard`, branch `feat/remote-touch` (`check()`: 6/6) |
| `big-remote-play-<data>-<hora>-any.pkg.tar` | `big-remote-play`, branch `feat/touch-controls` |

## Antes de instalar (cópia de segurança e reversão)

```sh
mkdir -p ~/brp-touch-backup
cp -a ~/.config/big-keyboard ~/.config/big-remote-play ~/brp-touch-backup/
pacman -Q sunshine big-remote-play big-keyboard > ~/brp-touch-backup/versoes.txt
ls /var/cache/pacman/pkg/ | grep -E 'big-(keyboard|remote-play)'   # pacotes antigos p/ reverter
```

Nenhuma configuração é sobrescrita: o Big Keyboard só acrescenta
`RemoteTouch` e `Output` ao `[VirtualGamepad]`; o Big Remote Play só
acrescenta `touch_mode` e `touch_open_on_connect`. O Sunshine não é
reinstalado.

## Instalar

```sh
cd ~/Documentos/Git/brp-touch-pacotes
sudo pacman -U big-keyboard-0.1.0-2-x86_64.pkg.tar big-remote-play-*-any.pkg.tar
sudo udevadm control --reload        # a regra nova do touchscreen
big-keyboard --quit; big-keyboard --daemon &   # recarrega só o Big Keyboard
```

Feche e abra o Big Remote Play. Não é preciso reiniciar o Sunshine, o
wlmaker nem o computador. A regra udev vale para touchscreens criados depois
de recarregá-la (o Sunshine cria um novo quando o celular conecta; se já
houver um de antes, reinicie o compartilhamento).

## Conferir

```sh
big-keyboard --help | grep -E 'remote-touch|output'
gdbus call --session -d io.github.big_windowmaker.BigKeyboard \
  -o /io/github/big_windowmaker/BigKeyboard -m org.gtk.Actions.Describe remote-touch-status
```

No Big Remote Play: **Compartilhar → Preferências → Mouse, teclado e
controles → Controles por toque → Compatibilidade**.

## Reverter

```sh
sudo pacman -U /var/cache/pacman/pkg/big-keyboard-0.1.0-1-*.pkg.tar* \
               /var/cache/pacman/pkg/big-remote-play-26.10.07-1217-*.pkg.tar*
cp -a ~/brp-touch-backup/big-keyboard ~/brp-touch-backup/big-remote-play ~/.config/
```

(Se os pacotes antigos não estiverem no cache, reinstale-os do repositório.)

## Moonlight Touch (celular)

Ver [validação no Android](08-validacao-android.md): compilar o APK exige JDK
17+, Android SDK 37 e NDK 29.0.14206865, que não estão nesta máquina.
