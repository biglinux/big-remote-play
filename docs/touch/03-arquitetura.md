# 03 — Decisão de arquitetura

## Alternativas comparadas

| | A — toque nativo até o overlay do Linux | B — controle na tela do Moonlight | C — canal próprio celular ↔ Big Remote Play |
|---|---|---|---|
| Visual | o do Big WM Virtual Keyboard, desenhado no Linux e visto no vídeo | o do Moonlight, desenhado no celular | qualquer |
| Funciona com o Moonlight oficial | **não** (precisa de cliente que envie toque) | **sim, hoje** | não (app novo) |
| Latência do toque à imagem do botão | toque vai ao host e o botão aceso volta pelo vídeo | imediata no celular | depende |
| Superfície de ataque nova | nenhuma (usa o canal criptografado do Moonlight) | nenhuma | listener, autenticação, pareamento novos |
| Game Window | o celular não vê o overlay | funciona | — |

**Decisão: A como caminho principal, B mantido e indicado como alternativa
sem instalação; C descartado** — tudo o que C exige (criptografia,
autenticação por sessão, ordem, anti-replay) já existe no canal de controle
do Moonlight; abrir outra porta só para isso aumentaria o risco sem ganho.

Um controle desenhado no Android (B) **não é** o overlay do Linux (A): são
dois controles distintos que o jogo vê como dois gamepads. A interface diz
para usar um de cada vez.

## Como A foi construído (sem depender do compositor)

```text
celular: dedos ──► Moonlight Touch (fork, setting "Send touch to the computer")
                     LiSendTouchEvent(id, x, y) — CANCEL/CANCEL_ALL corretos
                         │  canal de controle criptografado do Moonlight
                         ▼
Sunshine (native_pen_touch = enabled)
   └► libvirtualhid: /dev/input/eventN "libvirtualhid Touchscreen"
          (0..19200 × 0..10800 = o monitor transmitido)
                         │  leitura direta do kernel; EVIOCGRAB no modo exclusivo
                         ▼
big-keyboard (bk-rtouch) ─► bk_ctl_touch (um toque por slot) ─► bk-vpad (uinput)
   overlay layer-shell OVERLAY no monitor transmitido (--output=DP-1)       │
                                                                           ▼
                                                              jogo (SDL, Steam/Proton…)
Big Remote Play: modo, Sunshine native_pen_touch, abrir/fechar/soltar, prioridade do host
```

Por que ler o dispositivo direto em vez de esperar o compositor:

- o wlmaker não entrega toque a ninguém (e corrigir isso exigiria trocar o
  compositor da sessão em uso);
- no X11/KWin o toque fora dos botões viraria clique/movimento de mouse
  (requisito: "ausência de movimentos involuntários do mouse");
- é idêntico em X11 e Wayland, e a latência medida é de microssegundos.

Modos do Big Keyboard: `exclusive` (agarra o dispositivo; só o controle
recebe toques — usado em *On-screen controller*), `shared` (lê sem agarrar;
fora dos botões o toque segue para o desktop onde ele aceita toque — usado
em *Controller and touch*) e `off`.

## Segurança e ausência de botões presos

- Nada roda como root; nenhuma regra udev nova. O touchscreen é do assento
  (regra do Sunshine / grupo `input`); o Big Keyboard só abre dispositivos
  com nome `libvirtualhid … Touchscreen`, `INPUT_PROP_DIRECT` e eixos MT.
- O Big Keyboard só lê enquanto o controle está visível.
- Dispositivo some → todos os dedos dele são cancelados. `SYN_DROPPED` →
  estado relido com `EVIOCGMTSLOTS`.
- O Sunshine 2026.914 **não** cancela toques ao desconectar (ver
  [diagnóstico](02-diagnostico.md)). Três defesas independentes:
  1. o Big Remote Play chama `release` (e fecha o controle) quando o último
     dispositivo sai, e `release` quando sai um de vários;
  2. depois de um `release`, um dedo que o Sunshine ainda mantém só volta a
     valer se o celular voltar a movê-lo (nunca "escorrega" para um botão);
  3. o Moonlight Touch manda `CANCEL_ALL` ao perder o foco, ao sair e no
     início de cada stream; e há um patch de 8 linhas para o Sunshine
     ([integração](05-integracao.md)).
- *Host has priority*: no modo controle, o helper recebe `"touch": false` e
  nunca agarra o touchscreen/caneta — controles nunca são pausados.
- O Big Remote Play não altera o celular; a escolha do Moonlight continua do
  usuário.

## Fora do escopo, por decisão

- Gatilhos analógicos por pressão (o toque do celular não é um gatilho; o
  gatilho na tela é digital 0/1023, como no Moonlight).
- Implementar toque no wlmaker: útil para outros apps, mas exige reiniciar o
  compositor da sessão; não é necessário para este caminho.
