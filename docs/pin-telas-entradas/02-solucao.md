# 02 — Solução

## PIN: a senha sobrevive sem chaveiro

- `SunshineCredentials` guarda a senha em `~/.config/big-remote-play/sunshine/api-password`
  **somente quando nenhum chaveiro responde** (a consulta falha; não vale para
  chaveiro bloqueado nem para janela do chaveiro recusada). Arquivo 0600, pasta
  0700, escrita atômica, nunca em log, nunca em `sunshine.conf` nem no JSON de
  configurações.
- Primeiro Sunshine sem usuário: o app cria `sunshine` com senha aleatória,
  grava no arquivo **antes** de entregar ao Sunshine (nunca uma senha
  irrecuperável); se o Sunshine recusar, o arquivo é apagado.
- Quando um chaveiro aparece depois, a senha migra para ele e o arquivo some.
- **Segurança e acesso** diz onde a senha está. A chave “Lembrar neste
  computador” fica ligada também sem chaveiro.
- Usuário existente cuja senha o app não conhece: pedida **uma vez** (ou
  redefinida em **Alterar senha do Sunshine**); depois disso só o PIN.

## Entradas no Moonlight

- Patch no Sunshine (`pkgbuild/sunshine/brp-per-app-display-bitrate.patch`):
  `output-name` e `max-bitrate` por entrada do `apps.json`.
- `host/moonlight_entries.py` decide as entradas; `GameLibrary.sync_entries`
  aplica pela API (compartilhando) ou pelo arquivo; marca `brp-entry` para
  nunca mexer em apps da pessoa.
- **Preferências → Biblioteca de jogos → Entradas no Moonlight**: Desktop
  (sempre), Desktop Low (teto 1,5/3/5/8 Mbit/s), Steam Big Picture, uma entrada
  por tela com nome real e conector. Cada opção indisponível diz o motivo.
- Detalhes: [Moonlight entries](../moonlight-entries.md).

## Nome das telas

`utils/monitor_names.py`: dados reais do GTK → nome do EDID (com marca pelo
código PNP) → descrição sem número de série e conector → “Tela integrada” para
painéis de notebook. Usado em Compartilhar, nos identificadores de tela, nas
Preferências do servidor e em Conectar → Abrir em.

## Instalação compartilhada

`ui/dependency_installer.py`: uma sessão de instalação única; todo painel
mostra os **Detalhes técnicos** da instalação em andamento, continua sozinho
quando ela termina e, após falha, oferece o botão de novo.
