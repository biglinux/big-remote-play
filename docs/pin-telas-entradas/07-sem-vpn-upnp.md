# 07 — Jogar pela internet sem VPN (portas no roteador)

Pedido (2026-11-17): abrir as portas no roteador por script, para usar o Big
Remote Play sem VPN.

## Diagnóstico (rede real)

| Fato | Evidência |
|---|---|
| Roteador ZTE F6645P com UPnP IGD | `gatedesc.xml` na porta 52869: `WANIPConnection:1` |
| Sem CGNAT | IP externo do roteador `177.235.186.139` = IP público visto de fora |
| `upnpc -s` e o UPnP do Sunshine diziam “nenhum IGD” | o ufw descartava as respostas SSDP do roteador (`[UFW BLOCK] SRC=192.168.0.1 … UDP`) |
| A antiga opção UPnP só ligava o UPnP do Sunshine, sem conferir nada | `upnp = enabled` no `sunshine.conf` |

## Solução

`host/router_ports.py`: acha o roteador (SSDP e, se não responder, o endereço
de descrição no gateway), pede só as 6 portas de jogo (nunca a 47990 do
painel), com prazo de 2 h renovado a cada 30 min, **confere no roteador**,
compara com o IP público (CGNAT) e remove as próprias regras ao parar.
Compartilhar → Visão geral mostra **Sem VPN, pela internet** com o resultado e o
**Endereço para o Moonlight** (IP público e o domínio, quando aponta para ele).

## Verificado no roteador real

Abrir: as 6 portas listadas no roteador apontando para `192.168.0.235`,
descrição *Big Remote Play*. Fechar: as 6 removidas, a regra do ZeroTier
mantida. Falta: conectar o celular pelos dados móveis (pessoa).

Detalhes: [roteador](../router.md#upnp-big-remote-play-opens-the-ports-and-checks-them).
