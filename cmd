ls -la /portfolio-telegram-analytics

cp portfolio-telegram-analytics/.env.example portfolio-telegram-analytics/.env

nano portfolio-telegram-analytics/.env
chmod 600 portfolio-telegram-analytics/.env

chmod +x portfolio-telegram-analytics/scripts/*.sh

sudo cp portfolio-telegram-analytics/deploy/systemd/portfolio-*.service /etc/systemd/system/
sudo cp portfolio-telegram-analytics/deploy/systemd/portfolio-weekly.timer /etc/systemd/system/
sudo systemctl daemon-reload

sudo timedatectl set-timezone America/Montreal

sudo systemctl enable --now portfolio-bot.service
sudo systemctl enable --now portfolio-worker.service
sudo systemctl enable --now portfolio-weekly.timer




systemctl stop portfolio-bot.service
systemctl stop portfolio-worker.service
systemctl stop portfolio-weekly.service
systemctl stop portfolio-weekly.timer

systemctl disable portfolio-bot.service
systemctl disable portfolio-worker.service
systemctl disable portfolio-weekly.service
systemctl disable portfolio-weekly.timer

rm -f /etc/systemd/system/portfolio-bot.service
rm -f /etc/systemd/system/portfolio-worker.service
rm -f /etc/systemd/system/portfolio-weekly.service
rm -f /etc/systemd/system/portfolio-weekly.timer