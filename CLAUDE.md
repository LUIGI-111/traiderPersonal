# Trading Desk — contexto para Claude

Mesa de trading multi-agente con una "oficina" (panel web / app de escritorio) para ver qué hacen los bots.
Paquete Python en `src/tradingdesk/`. Usuario: no técnico, usa **Windows (PowerShell)**, habla **español**.

## Cómo trabajar con el usuario
- Responde en español, simple y por pasos. Un mensaje claro mejor que muchos. Da comandos de PowerShell.
- Nunca pidas ni muestres claves o contraseñas; nunca las pongas en commits, logs ni capturas.
- Antes de afirmar que algo funciona, ejecútalo o pruébalo (`pytest`, `tradingdesk serve --demo`, captura con Playwright).
- Sube cambios a `main` solo cuando el usuario lo pida o sea parte de la tarea. `git push origin main` recompila el .exe vía GitHub Actions.

## Comandos
```
pip install -e ".[dev,desktop]"
pytest -q                      # todos deben pasar
tradingdesk serve --demo       # oficina en http://127.0.0.1:8765 con datos sintéticos
tradingdesk-desktop            # ventana nativa (datos reales de Yahoo; --demo para sintéticos)
```
Datos del usuario (cartera, journal, `.env`): `%APPDATA%\TradingDesk` (Windows). Registro de errores: `desktop.log` ahí.

## Reglas de diseño que NO se rompen
1. **El entorno "real" nunca ejecuta órdenes.** Se fuerza en `server.py` (`start_run`, `execute=False`). Es solo seguimiento y recomendación.
2. **Bitso solo lectura** (`bitso.py`, llave con permiso de lectura). Tyba no tiene API pública: carga manual (`accounts.py`).
3. **Paper trading por defecto**; el riesgo lo decide código determinista (`risk.py`), no un LLM.
4. **Sin dependencia de pago:** los bots cuantitativos (`strategies.py`, `lab.py`) funcionan sin API. La IA de Claude es opcional (`TD_USE_LLM=1` + `ANTHROPIC_API_KEY`).
5. Backtests honestos: parámetros fijos (sin optimizar), señal ejecutada al día siguiente, costo 0,1%, misma ventana para todas las reglas, comparar siempre contra comprar y mantener. No prometer "estrategias exitosas".
6. Totales reales por moneda, sin convertir (no hay tasas de cambio todavía).

## Mapa del código
- `data.py` datos (Yahoo 5 años / `SyntheticMarket` para demo y tests) · `strategies.py` reglas · `lab.py` backtest + pesos de voto
- `agents.py` bots, analistas IA opcionales, gestor de portafolio (voto ponderado) · `desk.py` orquesta una ronda
- `risk.py` · `broker.py` (paper) · `accounts.py` + `bitso.py` (entorno real) · `office.py` estado para la UI · `server.py` HTTP local
- `static/index.html` UI de un solo archivo, **sin build**, estilo "glass" oscuro (tarjetas translúcidas, iconos con degradado, elevación al hover). Mantener ese estilo.
- `desktop.py` ventana pywebview · `tradingdesk.spec` PyInstaller · `.github/workflows/build-desktop.yml` compila Windows/Mac/Linux

## Ideas pendientes (no hechas)
Tasa de cambio y total consolidado · gráfica de patrimonio en el tiempo · tarea diaria automática · alertas ·
más estrategias y validación fuera de muestra (walk-forward) · conexión de Tyba solo si aparece una API.
