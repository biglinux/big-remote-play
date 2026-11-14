# 06 — Testes e correções

Data: 2026-11-14. Máquina: wlmaker (Wayland), 3 monitores, Sunshine
2026.914. "Laboratório real" = o Big Keyboard compilado do branch, rodando
na sessão gráfica de verdade (D-Bus e configuração isolados em diretórios
temporários, sem tocar na instância instalada), com o overlay no **DP-1**
(3440×1440) e um touchscreen uinput **idêntico ao do libvirtualhid** (mesmo
nome, eixos, 16 slots, `INPUT_PROP_DIRECT`, mesma sequência de eventos por
contato); o gamepad virtual resultante é lido do kernel com
`CLOCK_MONOTONIC`. Não substitui o celular: valida do touchscreen do Sunshine
em diante.

## Automatizados

| Suíte | Resultado |
|---|---|
| Big Keyboard `meson test` (keys, geom, config, controls, vpad, **rtouch**) | 6/6 OK (também no `check()` do `makepkg`) |
| `test-rtouch` — mutação: normalização errada | detectada (falha em 3 checagens) |
| `test-rtouch` — mutação: END/BEGIN invertidos | detectada (falha em 2 checagens) |
| Big Remote Play `pytest` (Xvfb + D-Bus) | ver contagem final no [relatório](09-relatorio-final.md) |
| novos: `test_touch_control.py`, `test_touch_controls_ui.py`, 4 testes novos em `test_input_priority*.py` | todos passam |
| `make lint` (ruff), `make typecheck` (pyright: 0 erros, 6 avisos pré-existentes), `make check-i18n` (32 catálogos, 0 achados), `make metadata-check` | passam |

Cobertura do `test-rtouch`: um dedo, cinco dedos com identidades, slot
reaproveitado no mesmo quadro (END antes de BEGIN), `SYN_DROPPED` + releitura,
cancelar tudo, slot inválido, nomes aceitos/recusados; cenários MT-01…MT-11
em 1920×1080, 3440×1440 e 2560×1080; precisão (centro de cada controle ≤ 1 px
e acerta o controle) em 3440×1440, 2560×1080, 1920×1080, 1080×1920 e
2560×1600.

## Laboratório real (sessão wlmaker)

| Cenário | Resultado |
|---|---|
| MT-01 A | PASSOU |
| MT-02 A+B | PASSOU |
| MT-07 A+B+Y | PASSOU |
| MT-09 soltar só o A | PASSOU (B e Y seguem) |
| MT-03 DIREITA + A + B; dedo 2 sai; dedo 1 sai | PASSOU (DIREITA+B; depois só B) |
| MT-04 diagonal + B, e girar para a diagonal oposta | PASSOU |
| MT-05 dois analógicos independentes | PASSOU (LX=32767, RY=−32768) |
| MT-10 analógico contínuo | PASSOU (0, 1924, 5793 … 32767, monotônico) |
| analógico volta ao centro, o outro segue | PASSOU |
| MT-06 analógico + RT | PASSOU |
| MT-08 cinco contatos (LT, RT, LB, X, analógico) | PASSOU |
| escorregar para o Y não aperta o Y | PASSOU |
| MT-11 dispositivo some com tudo apertado | PASSOU (botões soltos, gatilhos 0, analógicos ao centro) |
| MT-19 reconexão (dispositivo novo) | PASSOU |
| toque fora dos controles | PASSOU (nenhum evento: nada de mouse) |
| hotplug: touchscreen criado depois do controle aberto | PASSOU |

Integração Big Remote Play ↔ Big Keyboard (cliente D-Bus do checkout, mesma
sessão real): iniciar o daemon, `remote-touch=exclusive`, `overlay-output=DP-1`,
abrir → estado `active`; 17/17 cenários acima através dele; A seguro →
`release` → solto; o mesmo dedo volta a se mover → A de novo (dedo obsoleto
reaproveitado); fechar → `idle`; verificações `sunshine_touch`,
`touchscreen` (`/dev/input/event29`, o do Sunshine real), `controller`,
`uinput` = ok.

## Desempenho (processamento local, medido)

Do `SYN_REPORT` escrito no touchscreen ao evento no gamepad virtual
(timestamps do kernel, `CLOCK_MONOTONIC`), 500 amostras cada:

| Medida | média | mediana | p95 | p99 | máx | perdidos |
|---|---|---|---|---|---|---|
| apertar A | 0,022 ms | 0,018 ms | 0,040 ms | 0,054 ms | 0,985 ms | 0 |
| soltar A | 0,008 ms | 0,008 ms | 0,012 ms | 0,016 ms | 0,023 ms | 0 |
| mover analógico | 0,036 ms | 0,035 ms | 0,047 ms | 0,060 ms | 0,078 ms | 0 |
| apertar A com o analógico girando a 120 Hz (30 amostras) | 0,020 ms | 0,020 ms | 0,038 ms | 0,041 ms | 0,041 ms | 0 |

CPU do Big Keyboard: 0,0 % com o controle aberto e parado (3 s); 0,8 % com
720 movimentos a 120 Hz em 6 s. Sem polling (descritores no laço do GLib).

**Não medido:** latência fim a fim (dedo → rede → Sunshine → imagem no
celular). Depende do celular, da rede e do codificador; precisa do
aparelho ([roteiro](08-validacao-android.md)).

## Defeitos encontrados e corrigidos

| Defeito | Causa | Correção |
|---|---|---|
| Controle ligado ao touchscreen errado | havia dois `libvirtualhid Touchscreen` (o do Sunshine, retido após a sessão, e o do teste); o código pegava o primeiro | o overlay lê **todos** (até 4), cada um com identidades próprias |
| Botão preso após queda da rede | Sunshine 2026.914 não cancela toques ao desconectar e mantém o dispositivo | `release` pelo Big Remote Play + dedo obsoleto + `CANCEL_ALL` no Big Remote Play Controller + patch do Sunshine |
| Touchscreen sem permissão fora do grupo `input` | a regra do Sunshine não casa o nome `libvirtualhid Touchscreen` | regra udev estreita no pacote `big-keyboard` |
| *Host has priority* congelaria o controle | o helper agarrava o touchscreen junto com o mouse | `"touch": false` nos modos de controle |
| Exclusão de gesto de borda no lugar errado (fork Android) | aplicada à `streamView`, que com barras pretas não encosta na borda | aplicada à `backgroundTouchView` (tela inteira) |
| Texto falso no fork ("menu do Voltar") | o Moonlight não tem esse menu | o texto diz que o toque de 3 dedos fica desligado |
| Overlay no monitor errado (3 monitores) | GTK 3 no Wayland não dá o nome do conector | `xdg-output` (`bk-outputs.c`) |

## Não executado

- Celular Android real (nenhum aparelho no `adb devices`); APK não
  compilado (o download do JDK/SDK/NDK e o aceite da licença do SDK foram
  negados pela política de permissões desta sessão).
- Jogos reais (Steam/Proton, Lutris, Heroic), controle Bluetooth/USB junto,
  X11/Window Maker, KDE, Game Window, rotação e mudança de resolução no
  celular: pendentes ([roteiro](08-validacao-android.md)).
- Patch do Sunshine não compilado.
