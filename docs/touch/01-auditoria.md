# 01 — Auditoria: multitoque do celular até o jogo

Registro técnico da auditoria feita em 2026-11-14 para o controle por toque
(Moonlight no celular → Sunshine → Big WM Virtual Keyboard → jogo). Os demais
documentos desta pasta: [diagnóstico](02-diagnostico.md),
[arquitetura](03-arquitetura.md), [implementação](04-implementacao.md),
[integração](05-integracao.md), [testes](06-testes.md),
[instalação](07-instalacao.md), [validação no Android](08-validacao-android.md)
e o [relatório final](09-relatorio-final.md).

## Ambiente auditado

| Item | Valor |
|---|---|
| Distribuição | Big WindowMaker (BigCommunity, base Manjaro), kernel 6.18 |
| Sessão | **wlmaker, Wayland** (`XDG_CURRENT_DESKTOP=wlmaker`), XWayland em `:0` |
| Monitores | HDMI-A-1 3440×1440 (0,0), **DP-1** 3440×1440 (3440,0, o compartilhado), DP-2 2560×1080 (6880,0) |
| Sunshine | `2026.914.233613-3` (libvirtualhid estático) |
| Big Remote Play instalado | `26.10.07-1217`; checkout no branch `feat/touch-controls` |
| Big WM Virtual Keyboard | pacote `big-keyboard 0.1.0-1`; repositório `big-windowmaker/big-keyboard` |
| wlmaker | `0.8.r80.g7481afe-36` (repositório local `big-windowmaker/wlmaker`) |
| GTK | 3.24.52 (Big Keyboard), 4.x/libadwaita (Big Remote Play) |
| `/dev/uinput` | `root:input 0660` + `uaccess` (regra do Sunshine) |

## Componentes e versões upstream lidos

| Projeto | Commit lido | O que importa |
|---|---|---|
| moonlight-android | `b48494c` (12.2) | `Game.java`: `trySendTouchEvent()` existe, mas a chamada continua **comentada** (linhas ~2035–2041) com a nota "Re-enable native touch when have a better solution for handling cancelled touches from Android gestures and 3 finger taps". |
| moonlight-common-c | `94c65fd` | `LiSendTouchEvent()` com id do ponteiro, x/y normalizados, pressão, área, rotação; eventos `DOWN/MOVE/UP/CANCEL/CANCEL_ALL/HOVER`. Só envia se o host anuncia `LI_FF_PEN_TOUCH_EVENTS`. |
| Sunshine | `657f9b6` | `native_pen_touch` (padrão ligado). No Linux, `virtualhid_input.cpp` cria por cliente um touchscreen uinput pelo libvirtualhid. |
| libvirtualhid | `2234c00` | Touchscreen `libvirtualhid Touchscreen`: protocolo B, 16 slots, `ABS_MT_POSITION_X/Y` 0..19200 × 0..10800, `INPUT_PROP_DIRECT`. Posição = **normalizada ao monitor transmitido** (sem o deslocamento no desktop). No KDE ainda pede ao KWin, por D-Bus, para mapear o dispositivo à saída. |

## O que cada parte já fazia

- **Moonlight Android oficial**: toque vira mouse (trackpad ou "mouse
  absoluto"); dois dedos = clique direito; três dedos = teclado. Tem um
  **controle na tela próprio** (setting *Show on-screen controls*), multitoque
  de verdade, que envia estado de gamepad — funciona hoje, sem alterações.
- **Sunshine**: aceita toque nativo e o entrega como touchscreen virtual.
- **wlmaker**: `src/input/manager.c` ignora dispositivos de toque
  (`case WLR_INPUT_DEVICE_TOUCH: break;`) e nunca anuncia
  `WL_SEAT_CAPABILITY_TOUCH`. Nenhum cliente Wayland recebe toque nesta sessão.
- **Big WM Virtual Keyboard**: `bk-controls.c` já é multitoque (um toque por
  `GdkEventSequence`, dedo preso ao controle onde começou, END/CANCEL
  soltam); `bk-vpad.c` cria o gamepad uinput; `bk-overlay.c` recebe toques
  pelo GTK. O relatório de QA dele (`docs/qa-2026-11-14.md`) registra que
  multitoque com dedos reais **não foi executado**.
- **Big Remote Play**: *Host has priority* (`host/input_priority.py`) agarra
  com `EVIOCGRAB` os dispositivos do Sunshine durante a pausa — **incluindo o
  touchscreen**; gamepads nunca são pausados. A UI só documentava os gestos
  do Moonlight (`docs/mobile-controls.md`).

## Licenças

| Componente | Licença | Consequência |
|---|---|---|
| Big Remote Play | ver `COPYING` do repositório | — |
| Big WM Virtual Keyboard | GPL-2.0-or-later | código novo no mesmo regime |
| Moonlight Android | **GPL-3.0** | o APK derivado deve vir com o código-fonte (branch `brp/native-touch`); `applicationId` próprio, como o próprio `build.gradle` pede |
| Sunshine | GPL-3.0 | o patch sugerido segue a licença do Sunshine |
| libvirtualhid | (upstream LizardByte) | só lido, não alterado |

Nenhum código foi copiado entre projetos de licenças diferentes.
