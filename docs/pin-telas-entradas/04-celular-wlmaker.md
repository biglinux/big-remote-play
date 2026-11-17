# 04 — Celular no wlmaker, atraso de 20 s e imagens no Moonlight

Relato (2026-11-17, celular + Big WindowMaker/Wayland): em todas as entradas o
mouse não funcionou e o controle na tela não apareceu; “DESKTOP” repetido no
Moonlight sem dizer qual monitor ou se era baixa conexão; jogos sem imagem.

## Diagnóstico (medido)

| Fato | Evidência |
|---|---|
| As entradas funcionaram | log: `Selected monitor [… (DP-2)]`, `App limits the video bitrate to 3000 Kbps` |
| Cada conexão esperava 20 s | `Executing Do Cmd … prepare` 06:09:28 → `Executing [Desktop]` 06:09:48; `kscreen-doctor -j` fora do Plasma: sem resposta (25 s) |
| O wlmaker descarta toque | `src/input/manager.c`: `case WLR_INPUT_DEVICE_TOUCH: break;`; ele mantém abertos os mouses virtuais do Sunshine (fds em `/proc/<wlmaker>/fd`) |
| O controle só abre em Controle/Híbrido | `touch_mode = automatic`; `uses_controller()` |
| “DESKTOP” repetido | as entradas usavam o `desktop.png` genérico do Sunshine |

## Correções

- **Automático no wlmaker** → Controle e toque com teclado + controle abertos; o
  dedo fora deles vira mouse (big-keyboard, `remote-touch=exclusive`). Sem o
  big-keyboard → Mouse (Sunshine sem toque nativo). Ver
  [controles no celular](../mobile-controls.md#desktops-that-do-not-accept-touch-wlmaker).
- **`kscreen-doctor` só no KDE Plasma**, limite de 5 s: o `prepare` no wlmaker
  passou de 20 s para 0,08 s.
- **Imagens**: logo da distro em Desktop/Desktop Low (selo “Low bandwidth ·
  3 Mbit/s”), número + nome + conector em cada monitor, capa real dos jogos
  (Steam, Heroic, Lutris) sem baixar nada. Ver
  [entradas no Moonlight](../moonlight-entries.md#pictures-in-moonlight).

## Ainda não validado

Celular real com o pacote novo (toque → mouse e controle no wlmaker); capas
aparecendo no Moonlight do celular.

## Depois: o controle na tela certa (2026-11-17)

Com uma entrada “02 - Monitor”, o teclado virtual, o controle na tela e o
mouse do dedo ficavam no monitor principal: o app passava ao big-keyboard a
tela escolhida em Compartilhar, e o registro da sessão só sabia a tela quando
o kscreen-doctor (KDE) respondia. Agora o registro guarda sempre a tela
capturada e o controle de toque usa a tela da sessão atual.

## Mouse com o teclado e o controle ocultos (2026-11-17)

Relato: ao ocultar o controle na tela e o teclado virtual, o mouse do
celular parava. Causa: o big-keyboard só lia os dedos do celular com o
teclado ou o controle aberto (`on_sync` em `bk-remote.c`), e o wlmaker
ignora o touchscreen do Sunshine; ninguém recebia os toques.

Correção:

- big-keyboard 0.2.3: modo de toque remoto `mouse`, o `exclusive` que
  continua lendo o celular com tudo oculto; então a tela inteira é o mouse
  em toque direto (o ponteiro vai até o dedo, tocar clica, dois dedos
  rolam, toque com segundo dedo é o clique direito).
- Big Remote Play pede `mouse` no wlmaker (Automático → Controle e toque),
  também quando o controle não abre sozinho; volta para `exclusive` quando
  o último celular sai. Um big-keyboard antigo recusa o valor e fica no
  `exclusive`, como antes.
- No KDE e no GNOME nada muda: o desktop recebe o toque do Sunshine.
