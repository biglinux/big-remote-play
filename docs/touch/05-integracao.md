# 05 — Integração

## Contratos entre os componentes

| De → para | Contrato | Validação |
|---|---|---|
| Big Remote Play Controller → Sunshine | `LiSendTouchEvent` (protocolo existente, `LI_FF_PEN_TOUCH_EVENTS`) | nenhuma extensão nova |
| Sunshine → Big Keyboard | nó evdev `libvirtualhid Touchscreen`, MT protocolo B, eixos 0..19200 × 0..10800 normalizados ao monitor transmitido | nome + `INPUT_PROP_DIRECT` + eixos MT conferidos ao abrir |
| Big Remote Play → Big Keyboard | D-Bus `io.github.big_windowmaker.BigKeyboard`, `/io/github/big_windowmaker/BigKeyboard`, `org.gtk.Actions`: `SetState remote-touch`, `SetState overlay-output`, `Activate set-gamepad (b)`, `Activate edit-gamepad`, `Activate release`, `DescribeAll` | valores validados nos dois lados; versão antiga detectada pela ausência das ações |
| Big Remote Play → Sunshine | `native_pen_touch` no `sunshine.conf` ao compartilhar (só nos modos que precisam) | Automático preserva a escolha manual |
| Big Remote Play → helper de prioridade | especificação JSON com `"touch": false` nos modos de controle | ausente = comportamento anterior; helper via PolicyKit continua aceitando só isso e o atraso |

O nome do monitor vem de `HostView._shared_output()` (conector GTK 4 da
sessão: `DP-1` no Wayland, nome RandR no X11), o mesmo que o Big Keyboard
resolve por xdg-output/RandR — não o nome que o Sunshine usa no KMS.

## Ciclo de vida durante o compartilhamento

1. Compartilhar começa: `native_pen_touch = enabled` (modos de toque/controle)
   ou `disabled` (Mouse); prioridade do host com a regra do toque.
2. Primeiro dispositivo começa a jogar (monitor de conexões): no modo
   controle, com "Abrir quando um dispositivo começar a jogar", o Big Remote
   Play inicia o Big Keyboard se preciso (`big-keyboard --daemon`), define
   modo e monitor e abre o controle.
3. O celular toca: o Sunshine cria/reaproveita o touchscreen; o Big Keyboard
   o detecta pelo udev e o lê.
4. Um de vários dispositivos sai: `release`.
5. O último sai, ou o compartilhamento para: `release` + fechar o controle
   (o gamepad virtual some do sistema).

## Patch sugerido ao Sunshine (não aplicado nesta máquina)

O Sunshine 2026.914 não cancela os toques nativos quando o cliente cai
([diagnóstico](02-diagnostico.md)). O Big Remote Play e o Big Keyboard já se
defendem, mas o lugar certo da correção é o Sunshine. Patch contra
`LizardByte/Sunshine@657f9b6` (**não compilado aqui**: o Sunshine desta
máquina é um pacote próprio e a compilação completa não coube nesta sessão):

```diff
--- a/src/input.cpp
+++ b/src/input.cpp
@@ -2374,6 +2374,14 @@ namespace input {
     reset_mouse_buttons();
     reset_keyboard_keys();
     reset_gamepads(input);
+
+    // Native touch: a client that drops with fingers down never sends their
+    // release, and the touchscreen outlives the session. Without this the
+    // contacts stay held (an on-screen controller keeps a button pressed).
+    if (input->client_context) {
+      platf::touch_input_t cancel {LI_TOUCH_EVENT_CANCEL_ALL, LI_ROT_UNKNOWN, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
+      platf::touch_update(input->client_context.get(), platform_touch_port(input->touch_port), cancel);
+    }
   }
```

## Isolamento

- Big Keyboard sozinho: inalterado com o controle fechado; com ele aberto e
  sem Sunshine, estado `waiting` e nada muda. Pode ser ajustado por
  `--remote-touch=off`.
- Big Remote Play sem Big Keyboard: modo Automático = comportamento antigo;
  a folha diz "Instale o Big WM Virtual Keyboard (pacote big-keyboard)" e
  desativa os botões.
- Moonlight oficial: nada muda (não envia toque nativo). O controle na tela
  do próprio Moonlight é indicado como alternativa.
