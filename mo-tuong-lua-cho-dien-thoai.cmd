@echo off
chcp 65001 >nul
rem Mở cổng 41900 (chỉ mạng Private) để điện thoại cùng Wi-Fi vào được Idea Note.
rem Bấm đúp file này, Windows hỏi quyền Admin -> bấm Yes.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -Command "if (Get-NetFirewallRule -DisplayName 'Idea Note 41900' -ErrorAction SilentlyContinue) { 'Da co san luat tuong lua Idea Note.' } else { New-NetFirewallRule -DisplayName 'Idea Note 41900' -Direction Inbound -Protocol TCP -LocalPort 41900 -Action Allow -Profile Private | Out-Null; 'Da mo cong 41900 cho mang Private. Dien thoai quet QR trong app la vao duoc.' }"
pause
