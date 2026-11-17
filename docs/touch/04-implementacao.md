# 04 — Implementação

Três repositórios, cada mudança no seu lugar ([arquitetura](03-arquitetura.md)).

## Big WM Virtual Keyboard — branch `feat/remote-touch`

| Arquivo | O que faz |
|---|---|
| `src/input/bk-rtouch.{c,h}` (novo, núcleo sem GTK) | Decodificador do protocolo multitoque B: `ABS_MT_SLOT`, `ABS_MT_TRACKING_ID`, `ABS_MT_POSITION_X/Y`, `SYN_REPORT`. Emite por quadro, nesta ordem, END (dedos que saíram), BEGIN (que chegaram) e MOVE (que se moveram) — um botão solto e outro apertado no mesmo quadro nunca se sobrepõem. `SYN_DROPPED` → ignora até o próximo `SYN_REPORT` e relê o estado (`EVIOCGMTSLOTS`). Abertura valida nome, `INPUT_PROP_DIRECT` e eixos MT, lê os limites e agarra (`EVIOCGRAB`) no modo exclusivo; lê o slot atual (`EVIOCGABS(ABS_MT_SLOT)`). `bk_rt_find_nodes()` lista todos os touchscreens remotos; um monitor udev ("udev", depois das ACLs) avisa entradas e saídas. |
| `src/ui/bk-overlay.c` | Até 4 touchscreens remotos ao mesmo tempo (compartilhamento normal + tela privada do Game Window). Cada dedo tem identidade própria (endereço único por dispositivo×slot) e vai para `bk_ctl_touch()` em pixels: `px = nx × largura do overlay`. Só lê com o controle visível. Editor também aceita o dedo remoto. Lendo sem exclusividade, ignora o toque que o compositor entrega ao GTK (o mesmo dedo não vale duas vezes). Dedos soltos à força ficam "obsoletos" até o celular movê-los de novo. Jogar ↔ editar solta tudo. Escolhe o monitor pelo conector. |
| `src/ui/bk-outputs.c` (novo) | Nome do conector de cada `GdkMonitor`: `xdg-output` numa fila Wayland própria (o GTK 3 não o expõe); no X11, o modelo do monitor (nome RandR). |
| `src/ui/bk-app.{c,h}` | Ações D-Bus `set-gamepad` (b), `remote-touch` (s, com estado, valida `off/exclusive/shared`), `overlay-output` (s, valida o nome), estado `remote-touch-status`; opções `--remote-touch=` e `--output=`. |
| `src/core/bk-config.{c,h}` | `[VirtualGamepad] RemoteTouch` (padrão `exclusive`) e `Output` (validado: letras, números, `-_.`). |
| `data/70-big-keyboard-remote-touch.rules` (novo) | `uaccess` só para o nó virtual `libvirtualhid Touchscreen`. |
| `tests/test-rtouch.c` (novo) | Decodificador, cenários MT-01…MT-11, precisão do mapeamento (5 resoluções). |

Estados de `remote-touch-status`: `off`, `idle` (controle fechado),
`waiting` (sem touchscreen do Sunshine), `active` (exclusivo), `shared`,
`busy` (outro leitor o agarrou), `denied` (sem permissão), `error`.

## Big Remote Play — branch `feat/touch-controls`

| Arquivo | O que faz |
|---|---|
| `src/big_remote_play/host/touch_control.py` (novo, sem GTK) | Modos (`automatic`, `mouse`, `touch`, `gamepad`, `hybrid`) e o efeito real de cada um: `native_pen_touch` do Sunshine (Automático não mexe na escolha do usuário), modo do Big Keyboard, se *Host has priority* pode pausar o toque. Verificações a partir de evidência (config do Sunshine, `/sys/class/input`, `/dev/uinput`, sessão, D-Bus do Big Keyboard). Cliente D-Bus `BigKeyboard` (`org.gtk.Actions`, `NO_AUTO_START`, tempo limite). |
| `src/big_remote_play/ui/touch_controls.py` (novo) | Folha **Compartilhar → Preferências → Mouse, teclado e controles → Controles por toque**: modo com descrição, abrir/fechar/editar/soltar, "Abrir quando um dispositivo começar a jogar", compatibilidade em palavras com resumo "Pronto para jogar", e as duas formas de jogar no celular. Trabalho bloqueante em `Worker`; resultados velhos descartados. |
| `src/big_remote_play/ui/host_view.py` | Linha na página de Preferências; salva/carrega `touch_mode` e `touch_open_on_connect`; põe `native_pen_touch` na configuração do Sunshine ao compartilhar; ouve o monitor de conexões (1º dispositivo → abre o controle no monitor compartilhado; último sai → solta e fecha; um de vários sai → solta); ao parar de compartilhar → solta e fecha. |
| `src/big_remote_play/host/input_priority.py` | `classify(..., pause_touch=False)` ignora touchscreen e caneta do Sunshine; a especificação do helper ganhou `"touch"` (ausente = comportamento antigo). |
| `src/big_remote_play/ui/input_priority.py` | `set_pause_touch()`; reinicia o helper quando a regra muda. |
| `usr/share/big-remote-play/scripts/input-priority-helper.sh` | Comentário: o helper aceita também a regra do toque. |
| `src/big_remote_play/utils/dependencies.py` | componente `big_keyboard` no instalador compartilhado (botão **Instalar** na folha). |
| `locale/*.po`, `usr/share/locale/*/LC_MESSAGES/*.mo` | 53 mensagens novas, traduzidas nos 32 catálogos. |
| `tests/test_touch_control.py`, `tests/test_touch_controls_ui.py` (novos), `tests/test_input_priority*.py` | Comportamento dos modos, verificações, cliente D-Bus (barramento falso), UI e helper. |

## Big Remote Play Controller — `~/Documentos/Git/moonlight-android-touch`, branch `brp/native-touch`

Fork do Moonlight Android 12.2 (GPL-3.0), commit `c94196a`, documentado em
`BRP-TOUCH.md` no próprio repositório:

- opção **Send touch to the computer (multi-touch)** (`checkbox_native_touch`,
  desligada por padrão: sem ela, o comportamento é o do 12.2);
- `sendNativeTouch()` resolve os motivos pelos quais o upstream manteve o
  envio desligado: cancelamentos do Android viram `CANCEL`/`CANCEL_ALL` (nunca
  UP, que seria um toque), só ponteiros cujo DOWN foi enviado são
  reportados, o toque de três dedos fica desligado nesse modo, exclusão de
  gestos de borda (Android 10+), MOVE só quando a posição muda,
  `CANCEL_ALL` ao perder o foco, ao sair e no início de cada stream;
- host sem toque (`LI_ERR_UNSUPPORTED`) → volta à emulação de mouse;
- `applicationId` `io.github.bigremoteplay.controller`, nome
  *Big Remote Play Controller*, versão `12.2-brpc1` — instala ao lado do oficial.

Strings em inglês, pt-BR e pt.
