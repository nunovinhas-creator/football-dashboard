# /scan — Orquestrador de análise diária

Executa os 4 agentes do projecto **Matemática Da Bola** em sequência e apresenta uma tabela de estado final consolidada.

## Quando usar

Corre `/scan` para obter um snapshot completo do estado do sistema num único comando:
- Dados frescos do dia
- Picks qualificados com edge e CLV
- Estado do ROI e analytics
- Decisão sobre alerta Telegram

## Sequência de execução

### Passo 1 — Leitura de dados

Invoca o agente `data-fetcher`:

```
Lê o ficheiro docs/preds_*.json mais recente e docs/history.json.
Devolve o relatório completo conforme o teu formato de output.
Contexto do projecto: football-dashboard, thresholds v3, FROZEN_SINCE=2026-06-10.
```

Guarda o output como `DATA_REPORT`.

### Passo 2 — Análise de picks

Invoca o agente `model-runner` com o `DATA_REPORT` como contexto:

```
Contexto do data-fetcher:
[DATA_REPORT]

Aplica os thresholds v3 e identifica os top picks do dia.
Constrói a tripla diária se houver >= 3 picks qualificados.
Calcula edge vs Pinnacle onde disponível.
```

Guarda o output como `MODEL_REPORT`.

### Passo 3 — Verificação de analytics

Invoca o agente `analytics-tracker`:

```
Lê docs/analytics_report.txt, docs/analytics_report_oos.txt e docs/freeze_manifest.json.
Verifica o estado do ROI por mercado, CLV e thresholds v3.
Emite alertas se necessário.
```

Guarda o output como `ANALYTICS_REPORT`.

### Passo 4 — Decisão Telegram

Invoca o agente `telegram-notifier` com `MODEL_REPORT` e `ANALYTICS_REPORT` como contexto:

```
Contexto do model-runner:
[MODEL_REPORT]

Contexto do analytics-tracker:
[ANALYTICS_REPORT]

Avalia os picks contra as 5 regras de disparo.
Decide se deve ser enviado alerta e formata a mensagem Telegram.
```

Guarda o output como `TELEGRAM_REPORT`.

## Output final obrigatório

Após os 4 agentes terminarem, apresenta a tabela consolidada:

```
╔══════════════════════════════════════════════════════════════╗
║           SCAN REPORT — Matemática Da Bola                  ║
║           YYYY-MM-DD HH:MM WEST                              ║
╠══════════════════════════════════════════════════════════════╣
║ AGENTE              │ STATUS    │ RESULTADO                  ║
╠══════════════════════════════════════════════════════════════╣
║ 1. data-fetcher     │ ✅ OK     │ N jogos | N ligas | CLV: ✗ ║
║ 2. model-runner     │ ✅ OK     │ N picks | Tripla: ✓/✗      ║
║ 3. analytics-tracker│ ✅ OK     │ ROI 1X2 +X% BTTS +X%      ║
║ 4. telegram-notifier│ ✅ SEND   │ N picks para envio         ║
╠══════════════════════════════════════════════════════════════╣
║ DECISÃO FINAL       │ ENVIAR TELEGRAM / SEM PICKS / AGUARDAR║
╚══════════════════════════════════════════════════════════════╝

TOP PICKS DO DIA:
[tabela de picks do model-runner]

[mensagem Telegram formatada se SEND]

ANALYTICS:
[resumo do analytics-tracker]
```

Se algum agente falhar (ficheiro não encontrado, dados corrompidos), marca o passo como `❌ ERRO`, inclui a razão, e continua para os passos seguintes sem bloquear. Um erro num agente não deve impedir os restantes de correr.
