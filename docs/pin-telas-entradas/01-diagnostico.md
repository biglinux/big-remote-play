# 01 — Diagnóstico (2026-11-17)

Quatro relatos, investigados no PC real do mantenedor (três monitores LG,
sessão wlmaker/Wayland, Sunshine 2026.914 local, Moonlight Qt 6.2.0) antes de
qualquer mudança.

## 1. A janela “New connection request” não abria mais

| Fato medido | Onde |
|---|---|
| Não há Secret Service na sessão wlmaker: a libsecret carrega, mas toda consulta falha (`Cannot read credentials from the system keyring`) | log do app de 17/11, `busctl --user list` |
| O `sunshine.conf` do app não tem mais `sunshine_user`; o Sunshine tem um usuário administrador | `~/.config/big-remote-play/sunshine/sunshine.conf`, `~/.config/sunshine/sunshine_state.json` (sem ler a senha) |
| A rotina que detecta pedidos (`_pairing_upkeep`) só lê `GET /api/pin` com senha; sem senha devolve “não sei” e nada é mostrado | `ui/host_view.py` |
| Sem chaveiro, a senha digitada valia só até fechar o app (contrato antigo, até em teste) | `utils/sunshine_credentials.py`, `tests/test_sunshine_credentials.py` |

**Causa:** sem chaveiro, a senha do Sunshine não sobrevivia a uma nova sessão;
sem senha, o app não vê quem espera parear, e a janela do PIN nunca abre.
Nos ambientes KDE/GNOME (com chaveiro) o fluxo seguia funcionando — por isso
“antes aparecia”.

## 2. Só “Desktop” no Moonlight

Lido no código-fonte do Sunshine 2026.914: `output_name` (tela capturada) e
`max_bitrate` são valores únicos do servidor, lidos na partida
(`config::video`); o campo `output` de um app é o arquivo de log do comando;
`prep-cmd` não muda a captura. Entradas “02 - Monitor” ou “Desktop Low” seriam
cópias de “Desktop” com outro nome — o próprio projeto já documentava isso em
`docs/moonlight-multi-monitor.md`.

## 3. “Unknown Unknown” em Compartilhar → Imagem e captura → Tela

```text
GDK (wlmaker):  'DP-1' manufacturer='Unknown' model='Unknown' description='LG Electronics LG ULTRAWIDE 503AZFM72555 (DP-1)'
GDK (X11):      'DP-1' manufacturer=None      model='DP-1'
EDID (sysfs):   card0-DP-1 GSM 'LG ULTRAWIDE' · card0-DP-2 GSM 'LG HDR WFHD' · card0-HDMI-A-1 GSM 'LG ULTRAWIDE'
```

O código juntava fabricante + modelo do GTK. O EDID que o próprio monitor
envia é a fonte confiável.

## 4. Headscale: “Outra instalação está em andamento”, sem dados técnicos

```text
05:24:47 polkitd: ... FAILED to authenticate ... org.manjaro.pamac.commit [pamac install --no-confirm headscale]
05:24:47 polkit-agent-helper-1: pam_unix(polkit-1:auth): conversation failed
```

A trava “uma instalação por vez” é global, mas o registro técnico pertencia
só ao painel que iniciou a instalação. Com a página recriada, o novo painel
dizia apenas “Outra instalação está em andamento”, sem **Detalhes técnicos**,
e nunca era avisado do fim — o botão ficava desativado.

## 5. APK

O fork `moonlight-android-touch` (Big Remote Play Controller) ainda usava a
arte do Moonlight. Trabalho feito em paralelo no próprio repositório do APK
(ver [03 — validação](03-validacao.md)).
