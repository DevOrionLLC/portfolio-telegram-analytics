Install:
sudo cp deploy/systemd/portfolio-*.service /etc/systemd/system/
sudo cp deploy/systemd/portfolio-weekly.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now portfolio-bot.service
sudo systemctl enable --now portfolio-worker.service
sudo systemctl enable --now portfolio-weekly.timer

Logs:
journalctl -u portfolio-bot.service -f
journalctl -u portfolio-worker.service -f
journalctl -u portfolio-weekly.service -f