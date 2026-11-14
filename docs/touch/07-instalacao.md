# 07 — Instalação nesta máquina

## Estado antes

| Pacote | Versão instalada |
|---|---|
| `sunshine` | 2026.914.233613-3 |
| `big-remote-play` | 26.10.07-1217 |
| `big-keyboard` | 0.1.0-1 |
| `wlmaker` | 1:0.8.r80.g7481afe-36 |

## Estado depois (2026-11-14, instalado com `pkexec`)

| Pacote | Versão instalada |
|---|---|
| `big-keyboard` | 0.1.0-2 (branch `feat/remote-touch`), `Identity=xbox360` no `~/.config/big-keyboard/big-keyboard.conf` |
| `big-remote-play` | branch `feat/touch-controls` |
| `sunshine` | 2026.914.233613-**4**: o mesmo 2026.914 com a correção de toque do upstream (abaixo) |
| celular | Big Remote Play Controller ([validação](08-validacao-android.md)) |

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

## Sunshine com a correção do toque

Com o Sunshine 2026.914 o toque cai acima do dedo quando o vídeo tem barras
pretas ([testes](06-testes.md)). O upstream corrigiu em 2026.1113
(LizardByte/Sunshine@4062cb7). Use o 2026.1113 ou mais novo quando o seu
sistema o tiver. O pacote oficial 2026.1113 para Arch **não roda no Manjaro de
2026-11** (pede Qt 6.12 e OpenSSL 4); por isso, nesta máquina:

1. Cópia de segurança do pacote instalado (refeito a partir dos arquivos do
   sistema, com o `.INSTALL` original) e da configuração:
   `~/brp-touch-backup/sunshine-2026.914-3/`.
2. O mesmo `PKGBUILD` do AUR (commit `63d35f7`), `pkgrel=4`, com um passo no
   `prepare()` que troca só as duas linhas de `monitor_touch_port()` pelas do
   upstream e falha se não as encontrar; `appstream-util validate --nonet`
   (a validação tentava baixar uma imagem da internet).
3. `pkexec pacman -U sunshine-2026.914.233613-4-x86_64.pkg.tar.zst`.
4. **Parar o Sunshine que já estava rodando** e compartilhar de novo: o
   processo antigo continua com o programa velho na memória até sair.

Reverter: `sudo pacman -U ~/brp-touch-backup/sunshine-2026.914-3/sunshine-2026.914.233613-3-x86_64.pkg.tar.zst`.

## Big Remote Play Controller (celular)

Download, verificação e instalação: [README](../../README.md#your-phone-as-a-controller-multi-touch).
Compilar: [validação no Android](08-validacao-android.md).
