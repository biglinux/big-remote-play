# 08 — Aprovar um dispositivo do Headscale sem terminal

## O problema relatado

O celular, no app Tailscale com o servidor alternativo, mostrou o comando que
o Headscale sugere:

```text
headscale auth register --auth-id hskey-authreq-… --user USERNAME
```

- sem `sudo`: *permission denied* no socket `/var/run/headscale/headscale.sock`
  (só o root usa o socket);
- com `sudo`: *user not found*, porque `USERNAME` é um exemplo; o usuário
  criado pelo assistente nesta máquina é `ruscher`.

O Big Remote Play já tinha **Aprovar um dispositivo**, mas só com uma chave de
API do Headscale salva. Sem ela, a caixa ficava escondida e o app mandava usar
o terminal com `--user USER`.

## O que mudou

**Aprovar um dispositivo** aparece sempre que o Headscale está conectado: na
aba Dispositivos da página do Headscale, em **Adicionar dispositivo** e no
passo 7 do assistente. Basta colar o que o celular mostra (a linha inteira, o
link ou só o código) e clicar em **Aprovar**. O app tenta, nesta ordem:

1. **chave de API** salva: aprova direto pela API;
2. **servidor neste computador**: o sistema pede a senha de administrador
   (PolicyKit) e o ajudante `headscale-server-helper.sh register NOME CÓDIGO`
   cria o usuário, se faltar, e aprova;
3. **servidor em outro lugar**: mostra *Aprove no servidor* com o comando
   pronto, já com o código e o usuário verdadeiro, para copiar.

Mensagens: *pixel-8 foi adicionado à sua rede*; código expirado → começar o
login de novo no celular; janela de senha fechada → *Não aprovado* e como
tentar de novo.

## Segurança

- O ajudante aceita só um nome válido (`[a-z0-9][a-z0-9._-]{0,62}`) e um
  código de letras, números, `_` e `-` que não começa com `-`; os argumentos
  vão com `--auth-id=` e `--user=`, sem shell.
- A ação PolicyKit é a mesma do servidor Headscale (`auth_admin_keep`).

## Testes

- `tests/test_headscale_setup.py`: o ajudante recusa nome/código inválidos
  (`USERNAME`, `--force`, `;rm -rf /`); com um `headscale` falso cria o usuário
  que faltava e aprova; código expirado → `RESULT=rejected`; `approve_here` só
  roda quando o servidor é este computador.
- `tests/test_headscale_wizard.py`: sem chave de API aprova pelo servidor local
  com o usuário `ruscher`; senha recusada; servidor em outro lugar mostra o
  comando real, nunca `USERNAME`.
- `tests/test_connect_your_devices.py`: a página do Headscale conectada sempre
  mostra **Aprovar um dispositivo**.

Ainda falta testar com o celular real (código novo do app Tailscale).
