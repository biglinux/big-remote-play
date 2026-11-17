# 03 — Validação e o que ainda falta

## Testes automáticos

| Verificação | Resultado |
|---|---|
| Suíte completa (Xvfb + D-Bus) | 714 aprovados; 1 falha de texto fixo (nomes de entrada), corrigida com constantes e conferida com `check_hardcoded_text.py` (0) |
| `make lint`, `make typecheck` | sem erros (avisos de tipo: 6 → 4) |
| Novos testes | `test_moonlight_entries.py` (11), `test_monitor_names.py` (6), `test_install_session.py` (2), credenciais sem chaveiro (6), pareamento sem chaveiro (2) |

## No hardware real (PC de três monitores, wlmaker, 2026-11-17)

| Cenário | Evidência |
|---|---|
| Sessão sem chaveiro, Sunshine novo isolado (porta 48989) | `keyring absent: True`; usuário `sunshine` criado; `api-password` 0600 em pasta 0700 |
| Nova sessão do app | senha lida do arquivo (`location=file`); `GET /api/pin` → `200 [('roth', '127.0.0.1')]` — o dado que abre “New connection request” |
| Aprovação só com o PIN | `PinResult(ok=True, status=200)`; Moonlight Qt 6.2.0 pareado |
| Entrada “02 - Monitor” (Sunshine com patch) | `Selected monitor [LG HDR WFHD … (DP-2)]` com a tela global em DP-1 |
| Entrada “Desktop Low” (1,5 Mbit/s), cliente pediu 20 | `Streaming bitrate is 1500000` |
| Nomes das telas (Wayland real) | `01 · LG ULTRAWIDE · DP-1`, `02 · LG HDR WFHD · DP-2`, `03 · LG ULTRAWIDE · HDMI-A-1` |

## APK (Big Remote Play Controller 12.2-brpc3)

Repositório `moonlight-android-touch`, branch `brp/modern-ui` (detalhes em
`BRP-UI.md` lá): ícone do Big Remote Play (adaptativo, monocromático, TV),
tema AshyWall claro/escuro, cartões com estado em palavras, estado vazio,
“Como conectar”, “Sobre”, ícones para Desktop/Monitor/Steam e selo “Low
bandwidth”. Build debug e release assinados com a chave do Controller;
testes de unidade e lint do Gradle passaram; certificado igual ao brpc2.

## Ainda não validado

- Instalar o Sunshine com patch no sistema (precisa de `sudo pacman -U`) e
  rodar o fluxo completo pelo app instalado.
- Captura KMS e NVENC com as entradas por tela/baixa taxa (código lido; não
  medido).
- Steam Big Picture abrindo e fechando com uma Steam real.
- APK em telefone, tablet e TV reais (não há emulador na máquina): tema claro,
  ícone temático do Android 13, foco do D-pad, leitor de tela.
- Instalação do Headscale com a senha do PolicyKit confirmada (a falha
  relatada foi a autenticação recusada).
