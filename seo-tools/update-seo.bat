@echo off
chcp 65001 >nul
cd /d "%~dp0"
python update-seo.py --root ".." --config "seo-tools/seo-config.json"
if errorlevel 1 (
  echo.
  echo SEO 更新失敗，請查看上方錯誤訊息。
  pause
  exit /b 1
)
echo.
echo SEO metadata、sitemap.xml 與 robots.txt 已同步。
pause
