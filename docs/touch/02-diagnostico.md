# 02 — Diagnóstico: onde o multitoque se perdia

Cada camada foi verificada no código e, quando possível, na máquina real.

| Camada | Estado encontrado | Evidência |
|---|---|---|
| Android (Moonlight oficial 12.2) | **Não envia toque nativo.** Todo toque vira mouse; o 2º dedo vira clique direito; 3 dedos cancelam os outros e abrem o teclado. | `Game.java`, chamada a `trySendTouchEvent()` comentada. |
| Protocolo (moonlight-common-c) | Suporta tudo o que é preciso (ids estáveis, x/y normalizados, CANCEL por dedo e CANCEL_ALL). | `LiSendTouchEvent()`; nenhuma extensão nova é necessária. |
| Sunshine 2026.914 (Linux) | Cria `libvirtualhid Touchscreen` (uinput) e escreve slot/tracking id corretos. **Dois problemas:** o dispositivo **sobrevive** à sessão (entrada retida por cliente pareado) e `reset_input_state()` **não cancela** os toques nativos ao desconectar. | `virtualhid_input.cpp`, `input.cpp`; na máquina: cliente desconectou às 08:20:45 e `event29` (Touchscreen) continuava existindo às 08:35. |
| Linux input | Nó `/dev/input/eventN` com ACL `uaccess` pela regra `60-sunshine.rules` (`ATTRS{name}=="Sunshine (libvirtualhid)*"` não casa com `libvirtualhid Touchscreen`, mas o usuário está no grupo `input`). | `/proc/bus/input/devices`, `ls -l`. |
| Compositor | **wlmaker descarta o toque.** X11/Xorg e KWin entregariam, mas como toque/ponteiro emulado para quem está embaixo do dedo. | `wlmaker/src/input/manager.c:559`. |
| Big WM Virtual Keyboard | Lógica multitoque correta, mas só recebe toques do GTK — que, no wlmaker, nunca chegam. | `bk-overlay.c: on_touch`. |
| Gamepad virtual | Correto (uinput, botões deduplicados, some ao fechar o descritor). | `bk-vpad.c`, teste `vpad`. |
| Big Remote Play | *Host has priority* pausaria (agarraria) o touchscreen junto com o mouse: o controle congelaria enquanto alguém mexe no mouse local. | `input_priority.py: REMOTE_NAMES`. |

## Mapeamento de coordenadas

1. Moonlight normaliza o toque à `streamView` (a área do vídeo: barras pretas,
   proporção 16:9/18:9/20:9/21:9, rotação e recortes já ficam de fora) —
   `getStreamViewRelativeNormalizedXY()`.
2. Sunshine/libvirtualhid escrevem `x·19200`, `y·10800` no dispositivo,
   relativos ao **monitor transmitido**.
3. Logo, no host: `px = x / 19200 × largura lógica do monitor transmitido`.
   Escala fracionária não interfere (ambos os lados são normalizados).

O erro clássico — mapear sobre a resolução física ou sobre o desktop inteiro
— acontece quando o controle na tela está **em outro monitor**. Nesta máquina
há três monitores e o GTK 3 no Wayland não expõe o nome do conector
(`gdk_monitor_get_model()` devolve `Unknown`): era preciso resolver `DP-1`
de outra forma (xdg-output).

## Desconexão (o caso perigoso)

Rede cai com um dedo segurando "acelerar": o Moonlight não envia mais nada;
o Sunshine encerra a sessão mas mantém o touchscreen e o contato pressionado.
Sem tratamento, o botão fica preso até o celular voltar e tocar no mesmo slot.
