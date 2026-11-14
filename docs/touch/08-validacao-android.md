# 08 — Validação com o celular Android

Status (2026-11-14): **release 12.2-brpc2 publicada**
([download](https://github.com/ruscher/big-remote-play/releases/tag/controller-v12.2-brpc2),
[código](https://github.com/ruscher/moonlight-android-touch/tree/brp/native-touch)),
`io.github.bigremoteplay.controller`, arm64-v8a, armeabi-v7a, x86 e x86_64,
assinaturas v1+v2+v3 com a chave de release do Big Remote Play Controller
(certificado SHA-256 `A2:8A:FA:C5…71:C9:26`). Testado num celular real
([resultados](06-testes.md#celular-real-2026-11-14)); o roteiro abaixo,
item a item, ainda está **pendente**.

## 1. Gerar o APK (no computador)

Requisitos: JDK 17+ (ex.: Temurin 21), Android SDK com `platforms;android-37`,
`build-tools` e `ndk;29.0.14206865`. Aceitar a licença do Android SDK é uma
decisão sua (`sdkmanager --licenses`).

```sh
cd ~/Documentos/Git/moonlight-android-touch     # branch brp/native-touch
git submodule update --init --depth 1
export ANDROID_HOME=$HOME/Android/Sdk JAVA_HOME=$HOME/Android/jdk-21
./gradlew assembleNonRootDebug
```

Saída: `app/build/outputs/apk/nonRoot/debug/app-nonRoot-debug.apk`, assinado
com a chave de depuração gerada **no seu computador** (`~/.android/debug.keystore`),
não com a chave oficial do Moonlight. O build de depuração tem o id
`io.github.bigremoteplay.controller.debug` e instala **ao lado** do
Moonlight oficial e do release (nenhum substitui o outro). Para o release:
`./gradlew assembleNonRootRelease` e `apksigner sign` (ver `BRP-TOUCH.md` no
fork).

## 2. Instalar no celular

Ative a depuração USB, conecte o cabo e aceite a chave no celular:

```sh
adb devices
adb install -r app/build/outputs/apk/nonRoot/debug/app-nonRoot-debug.apk
```

Ou copie o APK e abra-o no celular (permitir "instalar apps desconhecidos"
só para o gerenciador de arquivos usado, e desligar depois).

## 3. Preparar o computador

1. Pacotes instalados ([instalação](07-instalacao.md)).
2. Big Remote Play → **Compartilhar → Preferências → Mouse, teclado e
   controles → Controles por toque** → **O que o toque faz: Controle na tela**;
   "Abrir quando um dispositivo começar a jogar" ligado.
3. **Compartilhar** (Área de trabalho completa, no monitor desejado).
4. Em Compatibilidade: tudo "ok", exceto "Toque do celular neste
   computador", que aparece quando o celular tocar.

## 4. No celular

1. Abra o **Big Remote Play Controller** e pareie com o computador (o pareamento do
   Moonlight oficial não é compartilhado).
2. Configurações → **Enviar toques ao computador (multitoque)**: ligado.
   Deixe **Mostrar controles na tela** desligado (senão o jogo vê dois
   controles).
3. Conecte em **Desktop**. O controle aparece no vídeo.

## 5. Roteiro (marque PASSOU/FALHOU com observações)

| ID | Ação | Esperado |
|---|---|---|
| MT-01 | um dedo no A | A pressionado (veja em `evtest` no "Big WM Virtual Gamepad" ou num testador de controle) |
| MT-02 | A e B juntos | os dois |
| MT-03 | direcional à direita + A | movimento e ação |
| MT-04 | diagonal + B | diagonal e ação |
| MT-05 | dois analógicos | independentes |
| MT-06 | analógico + RT | os dois |
| MT-07 | três botões | os três |
| MT-08 | cinco dedos | todos, sem trocas |
| MT-09 | soltar só o A | os outros seguem |
| MT-10 | deslizar o analógico | contínuo |
| MT-11 | segurar A e desligar o Wi-Fi do celular | A solto quando o Big Remote Play vê a sessão cair (alguns segundos) |
| MT-12 | girar o celular | o toque continua no botão certo |
| MT-13 | mudar a resolução no app (inclusive para uma que o celular não decodifica) | idem; nunca erro -5 (o app ajusta e avisa) |
| MT-14 | compartilhar outro monitor | o controle vai junto |
| MT-15/16 | Wayland (wlmaker/KDE) e X11 (Window Maker) | idem |
| MT-17 | jogo Steam/Proton | o jogo vê o controle |
| MT-18 | controle Bluetooth ao mesmo tempo | dois jogadores, sem conflito |
| MT-19 | sair e reconectar | nenhum botão preso; volta a funcionar |
| MT-20 | fechar o Big Remote Play jogando | nenhum botão preso; o gamepad virtual some |
| MT-21 | Moonlight oficial | comportamento de sempre (mouse) |
| MT-22 | Game Window | o celular não vê o controle: usar o do Moonlight (limitação) |
| MT-23 | escala do monitor | posições corretas |
| MT-24 | modo Mouse e voltar | alternância funciona (vale no próximo compartilhamento) |
| MT-25 | Big Remote Play Controller com Sunshine sem toque (`native_pen_touch = disabled`) | volta ao mouse, sem travar |

Ferramenta útil no computador: `evtest` (pacote `evtest`) no nó do "Big WM
Virtual Gamepad"; e `big-keyboard --release` para emergência.

## 6. Latência fim a fim (opcional)

Filme a tela do celular e o monitor do computador a 240 fps com um testador
de controle aberto no computador; conte quadros do toque até o botão acender
no monitor (processamento local já medido: mediana 0,018 ms; o restante é
rede, Sunshine e vídeo).
