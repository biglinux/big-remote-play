# 05 — Headscale: passo 6 de 7 (“Adicionar este computador”)

Relato (2026-11-17): ao abrir o Headscale, o assistente caía no passo 6 e pedia
uma “chave da API” sem explicar de onde viria. Pelo terminal, o comando da
página do Headscale falhou: sem `sudo`, *permission denied* no socket; com
`sudo`, *user not found* (o comando trazia o exemplo literal `--user USERNAME`).

## Diagnóstico

| Fato | Evidência |
|---|---|
| Headscale e Caddy foram configurados **nesta** máquina pelo assistente | `/etc/headscale/.brp-enabled-caddy` (06:25), `server_url: https://ruscher.dpdns.org`, `listen_addr: 127.0.0.1:8080` |
| A chave da API criada na instalação não foi guardada | `CredentialStore` só gravava no chaveiro; o wlmaker não tem Secret Service |
| O progresso salvo virou “Outro servidor” | `headscale-setup.json`: `"mode": "another_server"` |
| O comando para o dono usava `--user USER`/`USERNAME` | `_show_approval`: `self.progress.user or "USER"`, sem `sudo` e sem criar o usuário |

## Correções

- **Sem chaveiro**, as chaves de administração (Headscale, Tailscale,
  ZeroTier) vão para `private_network/secrets.json` (0600), só quando nenhum
  chaveiro responde; um chaveiro que aparecer depois assume as chaves.
- **Servidor nesta máquina é reconhecido** pelo `config.yaml` (legível sem
  root): o passo 6 mostra “O servidor roda neste computador” e um único botão,
  **Adicionar este computador**. Ele cria o usuário e a chave pelo helper
  (uma senha) e conecta.
- **Outro servidor**: três opções numeradas, a recomendada primeiro, com o
  comando para criar a chave da API e um botão para copiar.
- **Pedir aprovação**: dois comandos prontos, com `sudo` e o seu usuário real
  (`sudo headscale users create ruscher`, `sudo headscale auth register
  --auth-id … --user ruscher`).

## Ainda não validado

O clique real em **Adicionar este computador** nesta máquina, que pede a senha
do administrador e troca o perfil do Tailscale para o Headscale: depende de
você confirmar.
