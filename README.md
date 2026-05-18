# ⚽ Football Dashboard

Dashboard diário automático — jogos + predições ML + value betting vs Pinnacle.

**Gerado automaticamente todos os dias às 07:00 UTC via GitHub Actions.**

---

## O que faz

1. Busca todos os jogos do dia nas ligas cobertas
2. Para cada jogo: predições CatBoost (1X2, Over 2.5, BTTS, xG, score mais provável)
3. Detecta value onde `ML probability > Pinnacle implied + 3%`
4. Gera `docs/dashboard.html` (dark-mode, responsivo) e faz push para o repo
5. Envia resumo ao Telegram com os jogos com value

---

## Ligas cobertas

| Liga | País | Época activa |
|------|------|-------------|
| Premier League | 🏴󠁧󠁢󠁥󠁮󠁧󠁿 Inglaterra | Ago–Mai |
| La Liga | 🇪🇸 Espanha | Ago–Mai |
| Champions League | 🏆 Europa | Set–Mai |
| Europa League | 🏆 Europa | Set–Mai |
| Allsvenskan | 🇸🇪 Suécia | Abr–Nov |
| Eliteserien | 🇳🇴 Noruega | Abr–Nov |
| Veikkausliiga | 🇫🇮 Finlândia | Abr–Out |
| Brasileirão Serie A | 🇧🇷 Brasil | Abr–Dez |
| Brasileirão Serie B | 🇧🇷 Brasil | Abr–Nov |
| Copa do Brasil | 🇧🇷 Brasil | Fev–Out |
| Copa Libertadores | 🌎 América do Sul | Fev–Nov |
| Copa Sudamericana | 🌎 América do Sul | Fev–Nov |

---

## Secrets necessários

Adiciona em **GitHub → Settings → Secrets and variables → Actions → New repository secret**

| Secret | O que é |
|--------|---------|
| `BSD_API_KEY` | Token da BSD API (sports.bzzoiro.com — gratuito) |
| `TG_TOKEN` | Token do teu Telegram Bot |
| `TG_CHAT_ID` | O teu chat ID no Telegram |

---

## GitHub Pages (opcional)

Activa em **Settings → Pages → Source: Deploy from branch → Branch: main → Folder: /docs**

O dashboard fica disponível em:
`https://nunovinhas-creator.github.io/football-dashboard/dashboard.html`

---

## Estrutura do repo

```
football-dashboard/
├── dashboard.py                   ← script principal
├── README.md
├── .gitignore
├── docs/
│   └── dashboard.html             ← gerado automaticamente, não editar
└── .github/
    └── workflows/
        └── dashboard.yml          ← GitHub Actions
```

---

## Correr manualmente

**GitHub → Actions → Football Dashboard → Run workflow → Run workflow**

Útil para testar ou forçar actualização fora do horário automático.
