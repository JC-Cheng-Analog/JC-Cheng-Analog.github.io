@echo off
chcp 65001 >nul
cd /d "%~dp0"
python update-seo.py --root ".." --config "seo-tools/seo-config.json" --check
pause
