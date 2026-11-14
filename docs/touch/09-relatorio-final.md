# 09 — Relatório final: controle por toque

## Implementado

- **Big WM Virtual Keyboard** lê os dedos do celular direto do touchscreen
  virtual do Sunshine, um toque independente por dedo, e os transforma em
  botões, direcional (com diagonais), dois analógicos contínuos e gatilhos de
  um gamepad uinput. Funciona sem o compositor entregar toque (wlmaker não
  entrega), igual no X11 e no Wayland. Controle no monitor transmitido
  (`--output`), modos `exclusive`/`shared`/`off`, ações D-Bus, regra udev
  estreita.
- **Big Remote Play**: folha **Controles por toque** (modo, abrir/fechar/
  editar/soltar, compatibilidade verificada em palavras), efeito real de cada
  modo no Sunshine (`native_pen_touch`), abertura automática quando um
  dispositivo começa a jogar, liberação de botões quando ele sai, e *Host has
  priority* que não pausa mais o toque quando ele é um controle. 51 textos
  traduzidos nos 32 idiomas. O Big WM Virtual Keyboard, se faltar, é instalado
  pelo instalador do próprio app (botão **Instalar**).
- **Big Remote Play Controller** (fork GPL-3.0 do Moonlight Android 12.2, app separado):
  envio de toque nativo corrigido (cancelamentos, 3 dedos, gestos de borda,
  `CANCEL_ALL` em foco/saída/início). **Código pronto, APK não compilado.**
- Patch de 8 linhas para o Sunshine soltar toques ao desconectar (**não
  compilado**).

## Arquitetura

Celular → Big Remote Play Controller (`LiSendTouchEvent`) → Sunshine → touchscreen
uinput `libvirtualhid Touchscreen` → Big Keyboard (leitura direta,
`EVIOCGRAB`) → `bk_ctl_touch` → gamepad uinput → jogo. Detalhes e
alternativas: [arquitetura](03-arquitetura.md). Com o Moonlight **oficial**,
o caminho que funciona hoje é o controle na tela do próprio Moonlight
(desenhado no celular, não é o overlay do Linux).

## Arquivos

Ver [implementação](04-implementacao.md). Repositórios e branches:

| Repositório | Branch | Commits |
|---|---|---|
| `big-windowmaker/big-keyboard` | `feat/remote-touch` | `2f25183`, `28749b0` |
| `big-remote-play` | `feat/touch-controls` | este branch |
| `~/Documentos/Git/moonlight-android-touch` | `brp/native-touch` | `c94196a` |

Nada foi enviado a servidores remotos (sem push), nada foi mesclado no
`main`.

## Testes automatizados

- Big Keyboard: 6/6 suítes (inclui `rtouch`, sensível a mutações).
- Big Remote Play: suíte completa com Xvfb + D-Bus — **1792 passaram, 0
  falharam** (incluindo os novos testes de toque e o de links da
  documentação); `make lint`, `make typecheck` (0 erros), `make check-i18n`
  (0 achados), `make metadata-check` passam.

## Testes reais

Sessão wlmaker desta máquina, overlay no DP-1 (3440×1440), touchscreen
uinput com o perfil exato do libvirtualhid: **17/17 cenários** (MT-01…MT-11,
MT-19, toque fora dos botões, hotplug) e a integração Big Remote Play ↔ Big
Keyboard pelo D-Bus. Não usados: celular, jogos reais, controle físico,
X11/KDE, Game Window ([testes](06-testes.md)).

## Desempenho (local, medido)

Touchscreen → gamepad: apertar mediana 0,018 ms (p99 0,054 ms), soltar
0,008 ms, analógico 0,035 ms; 0 perdidos em 1.500; sob analógico a 120 Hz,
apertar p99 0,041 ms. CPU 0,0 % parado, 0,8 % movendo. A latência fim a fim
(rede + vídeo) **não** foi medida.

## Instalação

Pacotes gerados e testados (`big-keyboard 0.1.0-2`, `big-remote-play` do
branch) em `~/Documentos/Git/brp-touch-pacotes/`; **não instalados** — o
`sudo` pede senha. Comandos, cópia de segurança e reversão:
[instalação](07-instalacao.md).

## Teste com o Android

[Roteiro completo](08-validacao-android.md): compilar o APK, instalar ao
lado do Moonlight oficial, ligar **Enviar toques ao computador**, escolher
**Controle na tela** no Big Remote Play, conectar e seguir a tabela MT-01…MT-25.

## Limitações

- O Moonlight oficial não envia toque nativo; é preciso o Big Remote Play Controller.
- APK, celular real e jogos reais: pendentes.
- Game Window: o celular não vê o overlay (use o controle do Moonlight).
- Sunshine 2026.914 retém toques de uma sessão que caiu; o Big Remote Play
  solta os botões quando vê a sessão cair (segundos depois), o Moonlight
  Touch limpa ao reconectar; a correção definitiva é o patch do Sunshine.
- Gatilhos na tela são digitais (0 ou máximo).
- Touchscreen do Sunshine antigo (inputtino, "Touch passthrough") não é
  aceito: normalizava ao desktop inteiro.
- No modo exclusivo, toques fora dos botões não fazem nada (por projeto);
  use *Controle e toque* para tocar o desktop onde ele aceita toque (não no
  wlmaker).

## Próximos passos

1. `sudo pacman -U` dos dois pacotes ([instalação](07-instalacao.md)).
2. Compilar e instalar o Big Remote Play Controller ([roteiro](08-validacao-android.md)).
3. Executar MT-01…MT-25 com o celular e registrar os resultados aqui.
4. Levar o patch do Sunshine ao pacote/upstream.
