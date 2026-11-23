# Trading Desk — mesa de trading multi-agente

Equipo de agentes Claude que analizan acciones y proponen operaciones. **Paper trading por defecto (dinero simulado).** No es asesoría financiera.

```
Datos (yfinance) ─┬─> Analista técnico ───┐
                  ├─> Analista fundamental ├─> Gestor de portafolio ─> Gestor de RIESGO ─> Broker (paper)
                  └─> Analista de noticias ┘      (LLM, sintetiza)     (reglas duras)      + stop-loss
```

- **Bots cuantitativos** (en paralelo, **sin costo de API**): Tendencia (cruce 50/200), Momentum (6 meses), Reversión (RSI) y Ruptura (canal de 55 días). Cada uno devuelve señal + qué tan bien le ha ido históricamente en ese activo.
- **Laboratorio de estrategias:** antes de votar, cada regla se prueba contra ~5 años de historia real (con costos, ejecutando al día siguiente, parámetros fijos sin optimizar) y se compara con comprar y mantener. Se ve en la oficina. El peso de cada bot en el voto sale de su Sharpe en ese activo.
- **IA opcional:** con `TD_USE_LLM=1` y `ANTHROPIC_API_KEY` se suman analistas de Claude (técnico, fundamental, noticias) y Claude sintetiza la decisión. Es un extra de pago; sin él todo funciona igual.
- **Gestor de riesgo**: código determinista, no un LLM. Dimensiona por ATR, limita posición/exposición, confianza mínima, sin cortos, kill-switch por drawdown.
- **Journal** (`data/journal.jsonl`): cada decisión queda registrada para auditar.
- No necesita API de pago: la oficina funciona completa con los bots cuantitativos.

## Instalación en tu PC central

```bash
git clone <tu-repo> trading-desk && cd trading-desk   # o copia la carpeta
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                  # añade ANTHROPIC_API_KEY
pytest
```

## Uso

```bash
tradingdesk analyze AAPL MSFT NVDA   # analiza sin operar
tradingdesk run AAPL MSFT NVDA       # analiza y ejecuta en paper trading
tradingdesk portfolio                # estado de la cartera simulada
tradingdesk serve                    # abre LA OFICINA en http://127.0.0.1:8765
tradingdesk serve --demo             # igual, con datos sintéticos (sin red ni API key)
```

### Dos entornos: Prueba y Real
Arriba a la derecha de la oficina hay un selector (se recuerda tu elección):
- **Prueba (paper):** dinero simulado. La mesa puede operar sola en la cartera simulada.
- **Real (solo lectura):** tus cuentas reales para verlas juntas. **Nunca ejecuta órdenes**; el servidor lo fuerza aunque se lo pidan. La mesa analiza tus activos (ej. BTC-USD, AAPL) y solo recomienda.
  - **Bitso:** se sincroniza por API de **solo lectura**. Crea la llave en Bitso con permiso únicamente de lectura y ponla en `.env` (`BITSO_API_KEY`, `BITSO_API_SECRET`). Nunca la compartas ni la subas a git.
  - **Tyba** y cualquier otra cuenta: carga manual desde la oficina (Tyba no ofrece API pública). Para un fondo, unidades 1 y como valor unitario el saldo; con el costo total se calcula la ganancia.
  - Los totales se muestran **por moneda** (COP, MXN, USD), sin convertir entre ellas.
  - Los datos reales se guardan solo en tu PC: carpeta `data/real/` (o `real/` dentro de la carpeta de datos de la app de escritorio).

### La oficina (panel web)
Muestra en vivo qué hace cada agente (trabajando/libre, tareas hechas, último resultado), una ficha por activo estilo CRM (decisión, confianza, veredicto de riesgo, posición y P&L, historial), el feed de actividad, las operaciones y KPIs. Desde ahí también puedes lanzar rondas. Solo escucha en `127.0.0.1` (tu PC); no la expongas a internet.

Para que sea una "central" siempre activa: programa `tradingdesk run <tickers>` con cron (Linux/Mac) o el Programador de tareas (Windows) una vez al día tras el cierre.

## App de escritorio

```bash
pip install -e ".[desktop]"
tradingdesk-desktop            # ventana nativa con la oficina (añade --demo para datos de ejemplo)
```

Sus datos (cartera, journal y tu `.env` con la `ANTHROPIC_API_KEY`) viven en una carpeta propia por usuario:
Windows `%APPDATA%\TradingDesk`, macOS `~/Library/Application Support/TradingDesk`, Linux `~/.local/share/TradingDesk`.
Si algo falla, mira `desktop.log` en esa carpeta.

### Instalador/ejecutable
Se compila en cada sistema operativo con PyInstaller: `pip install -e ".[desktop,build]" && pyinstaller --noconfirm tradingdesk.spec`.
En GitHub, la acción **Build desktop app** (pestaña Actions → Run workflow, o al subir una etiqueta `v*`) compila Windows, macOS y Linux y deja los archivos como *artifacts*.
Nota: los ejecutables no están firmados; Windows/macOS mostrarán un aviso la primera vez.

## Hoja de ruta sugerida

1. Probar semanas en paper y revisar el journal antes de nada más.
2. Backtesting con histórico para validar la estrategia (hoy no incluido).
3. Broker real (Alpaca paper → live) detrás de la misma interfaz que `PaperBroker`, con confirmación humana.
4. Más agentes: macro, opciones, rotación sectorial, "abogado del diablo".
5. Panel web y alertas (Telegram/email).
