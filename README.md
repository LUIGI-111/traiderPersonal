# Trading Desk — mesa de trading multi-agente

Equipo de agentes Claude que analizan acciones y proponen operaciones. **Paper trading por defecto (dinero simulado).** No es asesoría financiera.

```
Datos (yfinance) ─┬─> Analista técnico ───┐
                  ├─> Analista fundamental ├─> Gestor de portafolio ─> Gestor de RIESGO ─> Broker (paper)
                  └─> Analista de noticias ┘      (LLM, sintetiza)     (reglas duras)      + stop-loss
```

- **Analistas** (en paralelo): cada uno devuelve `BUY/SELL/HOLD` + confianza + justificación.
- **Gestor de riesgo**: código determinista, no un LLM. Dimensiona por ATR, limita posición/exposición, confianza mínima, sin cortos, kill-switch por drawdown.
- **Journal** (`data/journal.jsonl`): cada decisión queda registrada para auditar.
- Sin `ANTHROPIC_API_KEY` funciona en modo offline con heurísticas (útil para probar).

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
