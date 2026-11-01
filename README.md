# <img src="https://i.imgur.com/DGVSxJL.png" width="50" height="50"/> Torrente On-Line Server Manager

![Stars](https://img.shields.io/github/stars/Lahrenheit/dedicatedtol?style=flat&label=Stars&color=ff69b4) ![Forks](https://img.shields.io/github/forks/Lahrenheit/dedicatedtol?style=flat&label=Forks&color=orange) ![Watchers](https://img.shields.io/github/watchers/Lahrenheit/dedicatedtol?style=flat&label=Watchers&color=blue)
![Tamaño del repo](https://img.shields.io/github/repo-size/Lahrenheit/dedicatedtol?style=flat) ![Última versión](https://img.shields.io/github/v/release/Lahrenheit/dedicatedtol?style=flat&color=success)
![Descargas totales](https://img.shields.io/github/downloads/Lahrenheit/dedicatedtol/total?style=flat&color=blue)

🌐 **[Español](#español) · [English](#english) · [Português](#português)**

Administrador visual (Python + tkinter, sin dependencias externas) para crear y controlar un servidor dedicado de **Torrente On-Line** (Virtual Toys, 2004).
Visual manager (Python + tkinter, no external dependencies) to create and control a **Torrente On-Line** dedicated server.

Basado en la versión server 1.1d

Repositorio / Repository: <https://github.com/Lahrenheit/dedicatedtol>

> Proyecto fan, no afiliado con Virtual Toys. / Fan project, not affiliated with Virtual Toys.
---
**Vista previa del programa:**

![Screenshot 55](https://raw.githubusercontent.com/Lahrenheit/dedicatedtol/refs/heads/main/1.png)
![Screenshot 56](https://raw.githubusercontent.com/Lahrenheit/dedicatedtol/refs/heads/main/2.png)
![Screenshot 57](https://raw.githubusercontent.com/Lahrenheit/dedicatedtol/refs/heads/main/3.png)
![Screenshot 58](https://raw.githubusercontent.com/Lahrenheit/dedicatedtol/refs/heads/main/4.png)

---



## Español

### Qué hace

- **Servidor**: inicia, detiene y reinicia `Dedicated.exe` (igual que el `.bat` original: `respawn.exe dedicated.exe` desde la carpeta del servidor), con respawn automático.
  - Panel de **Estado**: estado, puerto, mapa actual, **mapa próximo**, estado del matchmaker, PID y tiempo activo.
  - Registro de lo que escribe el dedicado y botones para sus comandos: `TIMELEFT`, `CONTINUE`, `NEXTMAP`, `PLAYER`, `MAP`, `KICK`, `VOTEMAP`, `HEADMODE`, `SAY`, `SPEED`, `MATRIX`, `CHAT`, `MATCH` y un campo para cualquier otro comando.
- **Mapas / Ciclo**: edita el `SCENE_LOOP` de `server.cfg` con los mapas `mp_*` que hay en la carpeta `scn` de `demo.vpk` (en mayúsculas) y muestra la vista previa (`texs\<mapa>-tbn.tex`). Solo aparecen los mapas que tienen imagen.
- **Configuración (server.cfg)**: todos los parámetros (puerto, nombre, contraseña, jugadores, tiempos, puntos, fuego aliado, etc.) con validación, importar/exportar JSON y **selección de la red base** del servidor.
- **Red / IP**: cambia la dirección del matchmaker dentro de `Main.dll` (botón *Parchear IP*), prueba el DNS, crea la regla del firewall y muestra las IP locales y la pública.
- **Idiomas**: español, português e English (selector arriba a la derecha).

### Requisitos

- Windows (el servidor y el cliente de Torrente On-Line son de Windows).
- Python 3.8 o superior **con tkinter** (incluido en el instalador oficial de Python), o el `.exe` compilado.
- Una carpeta del servidor dedicado de Torrente On-Line (`Dedicated.exe`, `respawn.exe`, `vtserver.dll`, `server.cfg`, `comun\bin\release`).
- Opcional: la carpeta del juego/cliente (con `demo.vpk` y sus DLL).

### Uso

```bat
python tol_server_manager.py
```

1. En la pestaña **Servidor**, abajo, indica la **carpeta del servidor** y la **carpeta del cliente** (donde está `demo.vpk`).
2. En **Mapas / Ciclo** arma el ciclo y pulsa *Guardar ciclo*; en **Configuración** ajusta los parámetros y pulsa *Guardar server.cfg*.
3. Elige la red base (Configuración) y, si hace falta, el matchmaker (Red / IP).
4. Pulsa **Iniciar servidor**.

### Cómo trata los archivos

- **DLL**: se copian a `<servidor>\comun\bin\release` (es el único sitio donde `Dedicated.exe` las lee). Antes de reemplazar una DLL se guarda la original como `<nombre>.dll.bak` (por ejemplo `Main.dll.bak`). El botón **Restaurar DLL originales** devuelve todo a su estado inicial.
- **Parchear IP**: solo cambia `Main.dll` cuando pulsas ese botón (máximo 15 caracteres; deja `Main.dll.bak`).
- **Ruta de datos**: sin la opción `-p`, `Dedicated.exe` lee la carpeta de `demo.vpk` del registro (`HKLM\SOFTWARE\Virtual toys\Torrente on-line`, valor `path`); el manager la iguala a la carpeta del cliente al iniciar (puede pedir permisos de administrador).
- **Red base**: se guarda como `netGUID` en el mismo registro que usa el cliente. El juego, al no ejecutarse como administrador, guarda su propia copia en el `VirtualStore` del usuario; el manager actualiza ambas.
- La configuración del manager se guarda en `tol_manager.json`, junto al programa.

### Compilar a .exe (auto-py-to-exe / PyInstaller)

- Script: `tol_server_manager.py`, modo *Window Based (hide the console)*.
- **Icono**: elígelo en el campo *Icon*; la ventana usa el mismo. También puedes dejar un `icon.ico` junto al `.exe`.

### Limitaciones conocidas

- La interfaz y el enlace con la consola de Dedicated (lectura de pantalla y envío de teclas) dependen de Windows; en otros sistemas solo se puede editar `server.cfg` y ver los mapas.
- El dedicado original puede perder alguna tecla al recibir comandos; el manager reenvía cada tecla hasta ver que apareció en la consola.
- Si el matchmaker no conecta: prueba *Probar DNS*, *Permitir en el Firewall*, y revisa la red base.

---

## English

### Features

- **Server**: start, stop and restart `Dedicated.exe` the same way the original `.bat` does (`respawn.exe dedicated.exe` from the server folder), with automatic respawn.
  - **Status** panel: state, port, current map, **next map**, matchmaker status, PID and uptime.
  - Live log of the dedicated server plus buttons for its commands: `TIMELEFT`, `CONTINUE`, `NEXTMAP`, `PLAYER`, `MAP`, `KICK`, `VOTEMAP`, `HEADMODE`, `SAY`, `SPEED`, `MATRIX`, `CHAT`, `MATCH`, and a free command box.
- **Maps / Cycle**: edit the `SCENE_LOOP` in `server.cfg` using the `mp_*` maps found in the `scn` folder of `demo.vpk` (uppercase), with a preview (`texs\<map>-tbn.tex`). Only maps that have a preview are listed.
- **Settings (server.cfg)**: every parameter (port, name, password, players, time, points, friendly fire, …) with validation, JSON import/export and **server base network** selection.
- **Network / IP**: change the matchmaker address inside `Main.dll` (*Patch IP* button), test DNS, create the firewall rule, show local and public IPs.
- **Languages**: Español, Português and English (selector at the top right).

### Requirements

- Windows.
- Python 3.8+ **with tkinter** (included in the official installer), or the compiled `.exe`.
- A Torrente On-Line dedicated server folder (`Dedicated.exe`, `respawn.exe`, `vtserver.dll`, `server.cfg`, `comun\bin\release`).
- Optional: the game/client folder (with `demo.vpk` and its DLLs).

### Usage

```bat
python tol_server_manager.py
```

1. On the **Server** tab (bottom), set the **server folder** and the **client folder** (the one containing `demo.vpk`).
2. Build the cycle in **Maps / Cycle** and press *Save cycle*; adjust the parameters in **Settings** and press *Save server.cfg*.
3. Pick the base network (Settings) and, if needed, the matchmaker (Network / IP).
4. Press **Start server**.

### How files are handled

- **DLLs** are copied to `<server>\comun\bin\release` (the only place `Dedicated.exe` reads them from). Before replacing a DLL, the original is saved as `<name>.dll.bak` (e.g. `Main.dll.bak`). **Restore original DLLs** puts everything back.
- **Patch IP** only changes `Main.dll` when you press that button (15 characters max; leaves `Main.dll.bak`).
- **Data path**: without `-p`, `Dedicated.exe` reads the `demo.vpk` folder from the registry (`HKLM\SOFTWARE\Virtual toys\Torrente on-line`, value `path`); the manager sets it to the client folder on start (it may ask for administrator rights).
- **Base network** is stored as `netGUID` in the same registry key the client uses. Because the game runs without administrator rights, it keeps its own copy in the user's `VirtualStore`; the manager updates both.
- Manager settings are saved in `tol_manager.json` next to the program.

### Building an .exe (auto-py-to-exe / PyInstaller)

- Script: `tol_server_manager.py`, *Window Based (hide the console)*.
- **Icon**: choose it in the *Icon* field; the window uses the same one. You can also drop an `icon.ico` next to the `.exe`.

### Known limitations

- The console link (screen reading and key sending) is Windows-only; elsewhere you can only edit `server.cfg` and browse maps.
- The original dedicated server may drop keystrokes; the manager resends each key until it shows up in the console.
- If the matchmaker does not connect: try *Test DNS*, *Allow in Firewall*, and check the base network.

---

## Português

Administrador visual (Python + tkinter, sem dependências externas) para criar e controlar um servidor dedicado de **Torrente On-Line**.

- **Servidor**: inicia, para e reinicia o `Dedicated.exe` como o `.bat` original, com painel de estado (incluindo o **próximo mapa**), log e botões para os comandos do dedicado.
- **Mapas / Ciclo**: edita o `SCENE_LOOP` com os mapas `mp_*` do `demo.vpk` e mostra a pré-visualização (`texs\<mapa>-tbn.tex`).
- **Configuração (server.cfg)**: todos os parâmetros, importar/exportar JSON e escolha da **rede base** do servidor.
- **Rede / IP**: altera o endereço do matchmaker no `Main.dll`, testa o DNS e cria a regra do firewall.
- **DLLs**: usa as DLLs do servidor e as do cliente que faltarem; os originais ficam como `<nome>.dll.bak` e podem ser restaurados.
- **Idiomas**: Español, Português, English.

Requisitos: Windows e Python 3.8+ com tkinter (ou o `.exe` compilado). Uso: `python tol_server_manager.py`, depois indique a pasta do servidor e a pasta do cliente (onde está o `demo.vpk`).
