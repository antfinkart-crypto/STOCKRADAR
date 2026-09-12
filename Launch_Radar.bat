@echo off
title QuantAlpha Server Running
cd /d "%USERPROFILE%\OneDrive\Desktop\Stockradar"
start http://127.0.0.1:8000
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause