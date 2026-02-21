# Portfolio Telegram Analytics (no Docker)

Telegram-first portfolio analytics:
- Users upload CSV via Telegram
- Bot prompts for column mapping (ticker/quantity)
- /run computes:
  - trailing return, vol, max drawdown, Sharpe (rf=2%)
  - compare vs SPY and IWM (configurable)
  - TSLA concentration
  - one-time static rebalance: TSLA shares -25%, redistribute pro-rata
  - plots: cumulative + drawdown
- Monday weekly summary via systemd timer

## 0) Create a Telegram bot token
Use BotFather:
- Create bot
- Copy token into `.env` as TELEGRAM_BOT_TOKEN

## 1) Install on DigitalOcean Ubuntu (no Docker)
### a) Create user + directories
```bash
sudo adduser --system --group --home /opt/portfolio-telegram-analytics portfolio
sudo mkdir -p /opt/portfolio-telegram-analytics
sudo chown -R portfolio:portfolio /opt/portfolio-telegram-analytics