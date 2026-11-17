# 06 — Celular pela rede Headscale

Relato (2026-11-17): com o Headscale funcionando, o celular adicionou
`ruscher.dpdns.org` em **Adicionar PC manualmente** no Moonlight e recebeu
*Erro de conexão*.

## Diagnóstico

| Fato | Evidência |
|---|---|
| `ruscher.dpdns.org` é o **servidor Headscale** (coordenação), no IP público de casa | `server_url` em `/etc/headscale/config.yaml` |
| O PC está na rede Headscale como **100.64.0.1** (`ruscher-wmaker.brp.internal`) | `tailscale status` |
| O celular **não** está na rede (o PC é o único dispositivo) | `tailscale status` |
| O firewall não bloqueia o Sunshine em nenhuma rede | verificação somente leitura do ufw: `blocked={}` |
| O endereço para o Moonlight ficava escondido em **Detalhes da conexão** | `internet_access_rows` |

As portas do Sunshine não ficam abertas na internet (nem devem): o celular
precisa entrar na rede privada e usar o endereço privado do PC.

## Correções

- **Compartilhar → Visão geral:** linha **Endereço para o Moonlight**
  (`100.64.0.1`) com **Copiar** e o aviso de que o endereço de um servidor
  não funciona no Moonlight.
- **Headscale → Adicionar outro dispositivo:** bloco **Num celular ou tablet**
  com os passos no app Tailscale (servidor alternativo, com QR Code do
  endereço do servidor), a aprovação na mesma página e o endereço privado
  que vai no Moonlight.

## Para você agora, no celular

1. Instale o **Tailscale** (Google Play).
2. Antes de entrar, no menu do app escolha **Use an alternate server** e
   digite `https://ruscher.dpdns.org`.
3. Entre; no PC, abra Headscale → **Adicionar outro dispositivo** e aprove o
   celular.
4. No Moonlight (ou no Big Remote Play Controller), apague o PC
   `ruscher.dpdns.org` e adicione **100.64.0.1**.
